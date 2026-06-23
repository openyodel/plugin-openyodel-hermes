"""
Yodel Protocol Platform Adapter for Hermes Agent.

Provides a Yodel-compatible HTTP endpoint that any Yodel-speaking device
can connect to. Converts Yodel requests to Hermes messages and streams
responses back as Server-Sent Events (SSE).

Protocol: https://github.com/openyodel/spec
"""

import asyncio
import json
import os
import time
import uuid
from http import HTTPStatus
from typing import Optional
from urllib.parse import urljoin

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig


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

# ── HTTP Helpers ──────────────────────────────────────────────────────


def parse_http_request(raw: bytes) -> tuple[str, str, dict[str, str], bytes]:
    """Parse a raw HTTP request. Returns (method, path, headers, body)."""
    parts = raw.split(b"\r\n\r\n", 1)
    header_block = parts[0]
    body = parts[1] if len(parts) > 1 else b""

    lines = header_block.split(b"\r\n")
    request_line = lines[0].decode("utf-8", errors="replace")
    method, path, _ = request_line.split(" ", 2)

    headers = {}
    for line in lines[1:]:
        if b":" in line:
            key, value = line.decode("utf-8", errors="replace").split(":", 1)
            headers[key.strip().lower()] = value.strip()

    return method.upper(), path, headers, body


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
    else:
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

    # ── BasePlatformAdapter interface ──────────────────────────────

    async def connect(self) -> bool:
        """Start the Yodel HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.bind_address,
            port=self.port,
        )
        self._mark_connected()
        self.logger.info(f"Yodel endpoint listening on http://{self.bind_address}:{self.port}")
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
        """
        Route agent response back to waiting HTTP handlers.
        Uses chat_id to correlate with pending Yodel requests.
        """
        queue = self._pending_responses.get(chat_id)

        if queue and metadata and metadata.get("_yodel_stream"):
            # Streaming mode: push chunks to queue
            chunk = metadata.get("_yodel_chunk", content)
            await queue.put(chunk)
            if metadata.get("_yodel_done"):
                await queue.put(None)  # Sentinel
            return SendResult(success=True)

        if queue:
            # Full response mode: deliver complete text
            await queue.put(content)
            await queue.put(None)  # Sentinel
            return SendResult(success=True)

        # Fallback: no pending HTTP handler — log the response
        self.logger.info(f"[yodel] Uncorrelated response for chat_id={chat_id}")
        return SendResult(success=True)

    async def get_chat_info(self, chat_id: str) -> dict:
        return {"name": f"yodel:{chat_id}", "type": "dm"}

    # ── HTTP Server ────────────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            # Read request with 4 MB limit for multimodal content (Issue #8)
            raw = await asyncio.wait_for(reader.read(4 * 1024 * 1024), timeout=30.0)
            if not raw:
                return

            method, path, headers, body = parse_http_request(raw)

            if method == "GET" and path == HEALTH_ENDPOINT:
                await self._handle_health(writer)
            elif method == "GET" and path == WELL_KNOWN_ENDPOINT:
                await self._handle_well_known(writer)
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
            self.logger.error(f"[yodel] Internal error: {e}")
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
        if self.api_key and token != self.api_key:
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

        # ── Extract Yodel headers ───────────────────────────────
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
        # Device context flows via MessageEvent.metadata below.

        event = MessageEvent(
            chat_id=chat_id,
            content=content,  # Clean user message — no device context prefix
            message_type=MessageType.TEXT,
            sender_id=yodel_device_id or chat_id,
            sender_name=yodel_device_id or "yodel-device",
            metadata={
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
                "tts_provider": tts_block.get("provider", ""),  # Issue #3: parse provider
                "tts_format": tts_block.get("format", "opus"),
            },
        )

        # ── Set up response correlation ─────────────────────────
        response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending_responses[chat_id] = response_queue

        # ── Fire message into Hermes (non-blocking) ─────────────
        asyncio.create_task(self.handle_message(event))

        # ── Stream SSE response ─────────────────────────────────
        try:
            await self._stream_sse_response(
                writer, response_queue, data.get("model", "hermes"),
                chat_id, yodel_session,
            )
        finally:
            self._pending_responses.pop(chat_id, None)

    async def _stream_sse_response(
        self,
        writer: asyncio.StreamWriter,
        queue: asyncio.Queue,
        model: str,
        chat_id: str,
        yodel_session: str = "",
    ) -> None:
        """Stream Hermes' response back as SSE chunks."""
        # Write SSE headers
        headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        )
        writer.write(headers.encode("utf-8"))
        await writer.drain()

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        sent_role = False

        try:
            # Wait for the full response text
            accumulated = ""
            response_received = False

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
                    break

                if chunk is None:
                    # Sentinel: response complete
                    response_received = True
                    break

                accumulated += chunk
                response_received = True

            if not response_received:
                writer.write(sse_event("[DONE]"))
                await writer.drain()
                return

            # Send role chunk first
            if not sent_role:
                writer.write(sse_chunk(chunk_id, "", model=model))
                sent_role = True

            # Stream accumulated text as chunks (word-by-word for smooth UX)
            words = accumulated.split(" ")
            for i, word in enumerate(words):
                spacer = " " if i > 0 and i < len(words) else ""
                delta = f"{spacer}{word}" if i > 0 else word
                writer.write(sse_chunk(chunk_id, delta, model=model))
                await writer.drain()
                await asyncio.sleep(0.02)  # Small delay for natural streaming feel

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
            self.logger.debug("[yodel] Client disconnected during streaming")
        except Exception as e:
            self.logger.error(f"[yodel] SSE stream error: {e}")


# ── Plugin Registration ───────────────────────────────────────────────


def check_requirements() -> bool:
    """Check if the Yodel adapter can run."""
    port = os.getenv("YODEL_PORT", "").strip()
    return bool(port)


def validate_config(config) -> bool:
    """Validate the Yodel configuration."""
    extra = getattr(config, "extra", {}) or {}
    port = os.getenv("YODEL_PORT") or extra.get("port")
    return bool(port)


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
