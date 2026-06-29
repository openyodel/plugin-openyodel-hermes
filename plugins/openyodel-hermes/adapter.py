"""
Yodel Protocol Platform Adapter for Hermes Agent.

Provides a Yodel-compatible HTTP endpoint that any Yodel-speaking device
can connect to. Converts Yodel requests to Hermes messages and streams
responses back as Server-Sent Events (SSE).

Protocol: https://github.com/openyodel/spec
"""

import asyncio
import hmac
import json
import logging
import os
import uuid
from http import HTTPStatus
from typing import Optional

logger = logging.getLogger(__name__)

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource


# ── Yodel Protocol Constants ──────────────────────────────────────────

YODEL_VERSION = 1
YODEL_ENDPOINT = "/v1/chat/completions"
HEALTH_ENDPOINT = "/v1/health"
WELL_KNOWN_ENDPOINT = "/.well-known/yodel.json"

YODEL_DEVICE_TYPES = {
    "ios", "android", "web", "car", "speaker",
    "terminal", "embedded", "agent_platform",
}

YODEL_CAPABILITIES = {
    "audio_out", "audio_in", "display", "haptic", "camera",
}

SESSION_MODES = {"ephemeral", "persistent"}

YODEL_INPUT_MODES = {"text", "voice"}

# ── HTTP Helpers ──────────────────────────────────────────────────────


def build_http_response(
    status: int,
    body: bytes = b"",
    headers: Optional[dict[str, str]] = None,
    content_type: str = "application/json",
) -> bytes:
    """Build an HTTP response."""
    status_text = HTTPStatus(status).phrase
    response = f"HTTP/1.1 {status} {status_text}\r\n"
    response += f"Content-Type: {content_type}\r\n"

    if headers:
        for key, value in headers.items():
            response += f"{key}: {value}\r\n"

    response += f"Content-Length: {len(body)}\r\n"
    response += "Connection: keep-alive\r\n"
    # CORS headers for web/PWA device support (#10)
    response += "Access-Control-Allow-Origin: *\r\n"
    response += "Access-Control-Allow-Headers: Authorization, Content-Type, X-Yodel-Version, X-Yodel-Device, X-Yodel-Agent, X-Yodel-Mode, X-Yodel-Input, X-Yodel-Session\r\n"
    response += "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
    response += "\r\n"
    return response.encode("utf-8") + body


def json_error(message: str, error_type: str, code: str) -> bytes:
    """Build an OpenAI-compatible error JSON body."""
    return json.dumps({
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
        }
    }).encode("utf-8")


# ── SSE Helpers ───────────────────────────────────────────────────────


def sse_event(data: str) -> bytes:
    """Build a single SSE data event."""
    return f"data: {data}\n\n".encode("utf-8")


def sse_chunk(
    chunk_id: str,
    delta_content: str,
    model: str = "hermes",
    finish_reason: Optional[str] = None,
) -> bytes:
    """Build an OpenAI-compatible SSE chunk."""
    choice = {"index": 0, "delta": {}, "finish_reason": finish_reason}
    if delta_content:
        choice["delta"]["content"] = delta_content
    elif finish_reason is None:
        choice["delta"]["role"] = "assistant"

    event = json.dumps({
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [choice],
    }, ensure_ascii=False)
    return sse_event(event)


# ── Adapter ───────────────────────────────────────────────────────────


class YodelAdapter(BasePlatformAdapter):
    """
    Yodel protocol adapter.

    Starts an HTTP server that accepts Yodel-compatible requests
    and routes them through the Hermes agent.
    """

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("openyodel"))
        extra = config.extra or {}
        self.port = int(os.getenv("YODEL_PORT") or extra.get("port", 8080))
        self.bind_address = os.getenv("YODEL_BIND_ADDRESS") or extra.get("bind_address", "0.0.0.0")
        self.api_key = os.getenv("YODEL_API_KEY") or extra.get("api_key", "")
        self._server: Optional[asyncio.AbstractServer] = None
        # Map: chat_id -> asyncio.Queue that receives response text, then None sentinel
        self._pending_responses: dict[str, asyncio.Queue[str]] = {}
        # Tracking for in-flight asyncio.Tasks to prevent GC (#15)
        self._tasks: set[asyncio.Task] = set()
        # Per-chat last sent text for delta computation in send_draft
        self._last_sent_per_chat: dict[str, str] = {}

        if not self.api_key:
            logger.warning("[yodel] No API key configured — endpoint will reject all requests. "
                                "Set YODEL_API_KEY environment variable.")

    # ── BasePlatformAdapter interface ──────────────────────────────

    async def connect(self, is_reconnect: bool = False) -> bool:
        """Start the Yodel HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.bind_address,
            port=self.port,
        )
        self._mark_connected()
        logger.info(f"Yodel endpoint listening on http://{self.bind_address}:{self.port}")
        return True

    async def disconnect(self) -> None:
        """Stop the Yodel HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Route agent response back to waiting HTTP handlers.

        When the stream consumer has already delivered content via
        ``send_draft()``, skip re-pushing the full text — only send the
        None sentinel to signal completion.  This avoids duplicate content
        in the SSE stream.
        """
        queue = self._pending_responses.get(chat_id)

        if queue and metadata and metadata.get("_yodel_stream"):
            # Streaming mode: push chunks to queue
            chunk = metadata.get("_yodel_chunk", content)
            await queue.put(chunk)
            if metadata.get("_yodel_done"):
                await queue.put(None)
                self._last_sent_per_chat.pop(chat_id, None)
            return SendResult(success=True)

        if queue:
            # Check if content was already streamed via send_draft()
            already_streamed = bool(self._last_sent_per_chat.get(chat_id, ""))
            if not already_streamed:
                await queue.put(content)
            # Always send sentinel to signal completion
            await queue.put(None)
            self._last_sent_per_chat.pop(chat_id, None)
            return SendResult(success=True)

        # Fallback: no pending HTTP handler — log the response
        logger.info(f"[yodel] Uncorrelated response for chat_id={chat_id}")
        return SendResult(success=True)

    async def get_chat_info(self, chat_id: str) -> dict:
        return {"name": f"yodel:{chat_id}", "type": "dm"}

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Yodel always supports draft streaming — all connections are SSE."""
        return True

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Push a streaming delta to the Yodel SSE writer.

        Called by GatewayStreamConsumer for each buffered text frame.
        Computes the delta from the last sent text and pushes it to the
        response queue so ``_stream_sse_response`` can emit an SSE event
        immediately — giving true token-by-token streaming to Yodel clients.
        """
        queue = self._pending_responses.get(chat_id)
        if not queue:
            return SendResult(success=True)

        # Compute delta: only send new content since last frame
        last_text = self._last_sent_per_chat.get(chat_id, "")
        if content.startswith(last_text):
            delta = content[len(last_text):]
        else:
            # Non-contiguous update (e.g. new segment after tool call)
            delta = content

        self._last_sent_per_chat[chat_id] = content

        if delta:
            await queue.put(delta)

        return SendResult(success=True)

    # ── HTTP Server ────────────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            # Read HTTP headers first, then body by Content-Length (#12 fix)
            header_raw = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=30.0
            )

            # Parse request line and headers
            header_text = header_raw.decode("utf-8", errors="replace").rstrip("\r\n")
            hlines = header_text.split("\r\n")
            request_line = hlines[0]
            method, path, _ = request_line.split(" ", 2)

            headers = {}
            for line in hlines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            # Read body exactly by Content-Length (avoids truncation)
            content_length = int(headers.get("content-length", "0"))
            max_body = 4 * 1024 * 1024  # 4 MB limit
            if content_length > max_body:
                resp = build_http_response(
                    413,
                    json_error("Request body too large", "validation_error", "body_too_large"),
                )
                writer.write(resp)
                await writer.drain()
                return

            body = b""
            if content_length > 0:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=30.0
                )

            if method == "GET" and path == HEALTH_ENDPOINT:
                await self._handle_health(writer)
            elif method == "GET" and path == WELL_KNOWN_ENDPOINT:
                await self._handle_well_known(writer)
            elif method == "OPTIONS":
                await self._handle_cors_preflight(writer)
            elif method == "POST" and path == YODEL_ENDPOINT:
                await self._handle_yodel_request(writer, headers, body)
            else:
                resp = build_http_response(
                    404,
                    json_error("Not found", "not_found_error", "not_found"),
                )
                writer.write(resp)
                await writer.drain()
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            # Issue #7: return 500 instead of silent disconnect
            logger.error(f"[yodel] Internal error: {e}")
            try:
                resp = build_http_response(
                    500,
                    json_error("Internal server error", "internal_error", "internal_error"),
                )
                writer.write(resp)
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        """GET /v1/health"""
        body = json.dumps({
            "status": "ok",
            "version": "1.0-draft",
            "gateway": "hermes",
        }).encode("utf-8")
        writer.write(build_http_response(200, body))
        await writer.drain()

    async def _handle_well_known(self, writer: asyncio.StreamWriter) -> None:
        """GET /.well-known/yodel.json"""
        body = json.dumps({
            "yodel_version": YODEL_VERSION,
            "gateway": "hermes",
            "endpoints": {
                "chat_completions": YODEL_ENDPOINT,
                "health": HEALTH_ENDPOINT,
            },
            "capabilities": [
                "streaming",
                "tts",
                "device_management",
                "agent_binding",
            ],
            "agents": [],
        }).encode("utf-8")
        writer.write(build_http_response(200, body))
        await writer.drain()

    async def _handle_cors_preflight(self, writer: asyncio.StreamWriter) -> None:
        """Handle CORS preflight OPTIONS request (#10)."""
        resp = build_http_response(204)  # No Content
        writer.write(resp)
        await writer.drain()

    async def _handle_yodel_request(
        self, writer: asyncio.StreamWriter, headers: dict[str, str], body: bytes
    ) -> None:
        """POST /v1/chat/completions — the main Yodel endpoint."""
        # ── Auth check ──────────────────────────────────────────
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            resp = build_http_response(
                401,
                json_error(
                    "Invalid or missing authentication token",
                    "authentication_error",
                    "invalid_auth_token",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        token = auth_header[7:]
        if not self.api_key:
            resp = build_http_response(
                503,
                json_error(
                    "Yodel endpoint not configured — API key missing on server",
                    "configuration_error",
                    "api_key_not_configured",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        if not hmac.compare_digest(token, self.api_key):
            resp = build_http_response(
                401,
                json_error(
                    "Invalid API key",
                    "authentication_error",
                    "invalid_api_key",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # ── Parse request body ──────────────────────────────────
        try:
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            resp = build_http_response(
                400,
                json_error(
                    "Invalid JSON in request body",
                    "validation_error",
                    "invalid_request",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # Validate stream:true — Yodel v1 is streaming-only (#11)
        if not data.get("stream", False):
            resp = build_http_response(
                400,
                json_error(
                    "Yodel v1 requires stream: true",
                    "validation_error",
                    "streaming_required",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # ── Extract Yodel headers ───────────────────────────────
        yodel_version = headers.get("x-yodel-version", "")
        yodel_agent = headers.get("x-yodel-agent", "")
        yodel_device_id = headers.get("x-yodel-device", "")
        yodel_mode = headers.get("x-yodel-mode", "ephemeral")
        yodel_input = headers.get("x-yodel-input", "text")
        yodel_session = headers.get("x-yodel-session", "")

        # ── Extract yodel body block ────────────────────────────
        yodel_block = data.get("yodel", {})
        tts_block = yodel_block.get("tts", {})
        device_block = yodel_block.get("device", {})
        input_lang = yodel_block.get("input_lang", "")

        device_type = device_block.get("type", "terminal")
        device_capabilities = device_block.get("capabilities", [])

        # Validate device type against known types (#18)
        if device_type not in YODEL_DEVICE_TYPES:
            resp = build_http_response(
                400,
                json_error(
                    f"Unknown device type: {device_type}. Valid types: {', '.join(sorted(YODEL_DEVICE_TYPES))}",
                    "validation_error",
                    "invalid_device_type",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # Validate capabilities against known set
        unknown_caps = [c for c in device_capabilities if c not in YODEL_CAPABILITIES]
        if unknown_caps:
            resp = build_http_response(
                400,
                json_error(
                    f"Unknown device capabilities: {', '.join(unknown_caps)}. Valid: {', '.join(sorted(YODEL_CAPABILITIES))}",
                    "validation_error",
                    "invalid_capabilities",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # Validate session mode
        if yodel_mode not in SESSION_MODES:
            resp = build_http_response(
                400,
                json_error(
                    f"Unknown session mode: {yodel_mode}. Valid: {', '.join(sorted(SESSION_MODES))}",
                    "validation_error",
                    "invalid_session_mode",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # Validate input mode
        if yodel_input not in YODEL_INPUT_MODES:
            resp = build_http_response(
                400,
                json_error(
                    f"Unknown input mode: {yodel_input}. Valid: {', '.join(sorted(YODEL_INPUT_MODES))}",
                    "validation_error",
                    "invalid_input_mode",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # ── Extract messages ────────────────────────────────────
        messages = data.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]

        if not user_messages:
            resp = build_http_response(
                400,
                json_error(
                    "No user message found in messages array",
                    "validation_error",
                    "missing_message",
                ),
            )
            writer.write(resp)
            await writer.drain()
            return

        # Take the last user message as the input
        last_user_msg = user_messages[-1]
        content = last_user_msg.get("content", "")

        # Handle multi-modal content (array of content parts)
        if isinstance(content, list):
            # Extract text parts, note image/audio parts
            text_parts = []
            has_media = False
            for part in content:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") in ("image_url", "image", "audio"):
                    has_media = True
            content = " ".join(text_parts)
            if has_media:
                content += "\n[Media attached — processing via Hermes vision]"

        # ── Build chat_id (must be unique per request!) ───────
        # Use device_id + random suffix to avoid collision on parallel requests
        # Session is tracked separately — NOT appended (Issue #1: corrupted session_id echo)
        device_base = yodel_device_id or "anon"
        request_suffix = uuid.uuid4().hex[:8]
        chat_id = f"{device_base}:{request_suffix}"

        # ── Build Hermes MessageEvent ───────────────────────────
        # Issue #9: Remove device context text injection — rely on
        # metadata + platform_hint. The user message stays clean.
        # Device context flows via SessionSource and raw_message.

        source = SessionSource(
            platform=self.platform,
            chat_id=chat_id,
            user_id=yodel_device_id or chat_id,
            user_name=yodel_device_id or "yodel-device",
        )

        yodel_metadata = {
            "yodel_version": yodel_version,
            "yodel_agent": yodel_agent,
            "yodel_device_id": yodel_device_id,
            "yodel_mode": yodel_mode,
            "yodel_input": yodel_input,
            "yodel_session": yodel_session,
            "yodel_device_type": device_type,
            "yodel_capabilities": device_capabilities,
            "yodel_input_lang": input_lang,
            "tts_requested": tts_block.get("requested", False),
            "tts_voice": tts_block.get("voice", ""),
            "tts_provider": tts_block.get("provider", ""),
            "tts_format": tts_block.get("format", "opus"),
        }

        event = MessageEvent(
            text=content,  # Clean user message — no device context prefix
            message_type=MessageType.TEXT,
            source=source,
            raw_message=yodel_metadata,
        )

        # ── Set up response correlation ─────────────────────────
        response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending_responses[chat_id] = response_queue

        # ── Fire message into Hermes (non-blocking) ─────────────
        task = asyncio.create_task(self.handle_message(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        # ── Stream SSE response ─────────────────────────────────
        try:
            await self._stream_sse_response(
                writer, response_queue, data.get("model", "hermes"),
                chat_id, yodel_session,
            )
        finally:
            self._pending_responses.pop(chat_id, None)
            self._last_sent_per_chat.pop(chat_id, None)

    async def _stream_sse_response(
        self,
        writer: asyncio.StreamWriter,
        queue: asyncio.Queue,
        model: str,
        chat_id: str,
        yodel_session: str = "",
    ) -> None:
        """Stream Hermes' response back as SSE chunks in real time.

        Pushes each chunk from the queue directly to the SSE writer as it
        arrives — no accumulate-then-replay.  This gives true first-token
        latency (typically <200ms) instead of waiting for the full response.
        """
        # Write SSE headers
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n"
        )
        writer.write(headers.encode("utf-8"))
        await writer.drain()

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        sent_role = False
        any_content = False

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    # No response within timeout — send error in stream
                    error_data = json.dumps({
                        "error": {
                            "message": "Response timed out",
                            "type": "backend_error",
                            "code": "timeout",
                        }
                    })
                    writer.write(sse_event(error_data))
                    await writer.drain()
                    return

                if chunk is None:
                    # Sentinel: response complete
                    break

                if not chunk:
                    continue

                any_content = True

                # Send role chunk before first content delta
                if not sent_role:
                    writer.write(sse_chunk(chunk_id, "", model=model))
                    sent_role = True

                # Stream this chunk immediately — no accumulation
                writer.write(sse_chunk(chunk_id, chunk, model=model))
                await writer.drain()

            # ── Stream complete — send termination events ──────────
            if not any_content:
                writer.write(sse_event("[DONE]"))
                await writer.drain()
                return

            # Send finish chunk
            writer.write(sse_chunk(chunk_id, "", model=model, finish_reason="stop"))

            # Send Yodel event with session info (Issue #1: echo original session_id)
            yodel_event = json.dumps({
                "yodel": {
                    "session_id": yodel_session or chat_id,
                }
            })
            writer.write(sse_event(yodel_event))

            # Send [DONE]
            writer.write(sse_event("[DONE]"))
            await writer.drain()

        except ConnectionResetError:
            logger.debug("[yodel] Client disconnected during streaming")
        except Exception as e:
            logger.error(f"[yodel] SSE stream error: {e}")


# ── Plugin Registration ───────────────────────────────────────────────


def check_requirements() -> bool:
    """Check if the Yodel adapter can run."""
    port = os.getenv("YODEL_PORT", "").strip()
    return bool(port)


def validate_config(config) -> bool:
    """Validate the Yodel configuration."""
    extra = getattr(config, "extra", {}) or {}
    port = os.getenv("YODEL_PORT") or extra.get("port")
    api_key = os.getenv("YODEL_API_KEY") or extra.get("api_key", "")
    if not port:
        return False
    if not api_key:
        # Warn but don't block — the adapter will reject requests at runtime
        pass
    return True


def _env_enablement() -> dict | None:
    """Auto-enable from environment variables."""
    port = os.getenv("YODEL_PORT", "").strip()
    api_key = os.getenv("YODEL_API_KEY", "").strip()
    if not port:
        return None
    seed = {"port": int(port), "api_key": api_key}
    bind_addr = os.getenv("YODEL_BIND_ADDRESS")
    if bind_addr:
        seed["bind_address"] = bind_addr
    home = os.getenv("YODEL_HOME_CHANNEL")
    if home:
        seed["home_channel"] = {"chat_id": home, "name": "Yodel Home"}
    return seed


def register(ctx):
    """Register the Yodel platform adapter with Hermes."""
    ctx.register_platform(
        name="openyodel",
        label="Open Yodel",
        adapter_factory=lambda cfg: YodelAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["YODEL_PORT"],
        install_hint="No additional dependencies required (stdlib only).",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="YODEL_HOME_CHANNEL",
        max_message_length=8000,
        platform_hint=(
            "You are chatting via a Yodel-compatible device. Device metadata "
            "(type, capabilities, input mode, language) is available in the "
            "message metadata. Adapt your response: be concise and avoid "
            "markdown for audio-only devices; use rich formatting for devices "
            "with display; expect image analysis for camera devices. "
            "Check message.metadata.yodel_device_type and "
            "message.metadata.yodel_capabilities to adapt."
        ),
        emoji="📡",
    )
