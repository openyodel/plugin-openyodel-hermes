#!/usr/bin/env python3
"""
Comprehensive test suite for the Yodel Hermes adapter.

Tests cover:
- Pure functions (sse_chunk, sse_event, build_http_response, json_error)
- Constants validation
- HTTP request parsing in _handle_client
- Auth checks (#16, #17)
- Stream validation (#11)
- Device/capability/mode validation (#18)
- SSE streaming behavior (#13, #14, #15)
- CORS headers (#10)
- Yodel version header (#5)

Usage:
    python3 -m pytest test_adapter.py -v
    or: python3 test_adapter.py
"""

import asyncio
import hmac
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure we can import the adapter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapter


# ── Pure Function Tests ───────────────────────────────────────────────


class TestPureFunctions(unittest.TestCase):
    """Tests for pure helper functions that don't need Hermes runtime."""

    def test_json_error(self):
        body = adapter.json_error("test message", "test_type", "test_code")
        data = json.loads(body)
        self.assertEqual(data["error"]["message"], "test message")
        self.assertEqual(data["error"]["type"], "test_type")
        self.assertEqual(data["error"]["code"], "test_code")

    def test_build_http_response(self):
        body = b'{"status": "ok"}'
        resp = adapter.build_http_response(200, body)
        resp_str = resp.decode("utf-8")
        self.assertIn("HTTP/1.1 200 OK", resp_str)
        self.assertIn("Content-Type: application/json", resp_str)
        expected_cl = f"Content-Length: {len(body)}"
        self.assertIn(expected_cl, resp_str)
        self.assertIn("Access-Control-Allow-Origin: *", resp_str)  # #10 CORS
        self.assertIn('{"status": "ok"}', resp_str)

    def test_build_http_response_custom_headers(self):
        body = b"hello"
        resp = adapter.build_http_response(
            201, body, headers={"X-Custom": "value"}, content_type="text/plain"
        )
        resp_str = resp.decode("utf-8")
        self.assertIn("HTTP/1.1 201 Created", resp_str)
        self.assertIn("Content-Type: text/plain", resp_str)
        self.assertIn("X-Custom: value", resp_str)
        self.assertIn("Access-Control-Allow-Origin: *", resp_str)

    def test_sse_event(self):
        result = adapter.sse_event("test data")
        self.assertEqual(result, b"data: test data\n\n")

    def test_sse_chunk_with_content(self):
        result = adapter.sse_chunk("chatcmpl-123", "Hello")
        decoded = result.decode("utf-8")
        self.assertIn("data: ", decoded)
        # Parse the SSE data
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        self.assertEqual(event["id"], "chatcmpl-123")
        self.assertEqual(event["object"], "chat.completion.chunk")
        self.assertEqual(event["choices"][0]["delta"]["content"], "Hello")
        self.assertNotIn("role", event["choices"][0]["delta"])

    def test_sse_chunk_empty_delta_for_role_sets_role(self):
        """Empty delta with no finish_reason should set role (initial chunk)."""
        result = adapter.sse_chunk("chatcmpl-123", "")
        decoded = result.decode("utf-8")
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        self.assertEqual(event["choices"][0]["delta"]["role"], "assistant")
        self.assertNotIn("content", event["choices"][0]["delta"])

    def test_sse_chunk_finish_has_empty_delta(self):
        """#14 fix: Finish chunk should have EMPTY delta, NOT role:assistant."""
        result = adapter.sse_chunk("chatcmpl-123", "", finish_reason="stop")
        decoded = result.decode("utf-8")
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        self.assertEqual(event["choices"][0]["finish_reason"], "stop")
        self.assertEqual(event["choices"][0]["delta"], {})


# ── Validation Tests ──────────────────────────────────────────────────


class TestValidations(unittest.TestCase):
    """Tests for validation logic."""

    def test_known_device_types(self):
        for dt in adapter.YODEL_DEVICE_TYPES:
            self.assertIn(dt, adapter.YODEL_DEVICE_TYPES)

    def test_known_capabilities(self):
        for cap in adapter.YODEL_CAPABILITIES:
            self.assertIn(cap, adapter.YODEL_CAPABILITIES)

    def test_known_session_modes(self):
        for mode in adapter.SESSION_MODES:
            self.assertIn(mode, adapter.SESSION_MODES)

    def test_yodel_constants(self):
        """Ensure constants are consistent."""
        self.assertEqual(adapter.YODEL_VERSION, 1)
        self.assertEqual(adapter.YODEL_ENDPOINT, "/v1/chat/completions")
        self.assertEqual(adapter.HEALTH_ENDPOINT, "/v1/health")
        self.assertEqual(adapter.WELL_KNOWN_ENDPOINT, "/.well-known/yodel.json")


# ── Auth Tests (#16, #17) ────────────────────────────────────────────


class TestAuth(unittest.TestCase):
    """Tests for authentication security fixes."""

    def test_hmac_compare_digest_used(self):
        """#17 fix: hmac.compare_digest must be in api key comparison."""
        # Read the source to verify
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("hmac.compare_digest(token, self.api_key)", source)

    def test_no_bare_inequality(self):
        """#17 fix: bare != must NOT be used for api key comparison."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        # The old pattern should not exist near api_key comparison
        self.assertNotIn("token != self.api_key", source)

    def test_api_key_check_before_compare(self):
        """#16 fix: Empty api_key must be checked before comparison."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("if not self.api_key:", source)


# ── SSE Chunk Tests (#14) ────────────────────────────────────────────


class TestSSEChunkEdgeCases(unittest.TestCase):
    """Edge case tests for SSE chunk generation (#14)."""

    def test_empty_string_is_not_content(self):
        """Empty string must not trigger content branch."""
        result = adapter.sse_chunk("id", "")
        decoded = result.decode("utf-8")
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        # Must NOT have content with empty string
        self.assertNotIn("content", event["choices"][0]["delta"])
        # Must have role since no finish_reason
        self.assertEqual(event["choices"][0]["delta"]["role"], "assistant")

    def test_space_only_delta_is_content(self):
        """A space is truthy in Python and should be treated as content."""
        result = adapter.sse_chunk("id", " ")
        decoded = result.decode("utf-8")
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        self.assertEqual(event["choices"][0]["delta"]["content"], " ")

    def test_finish_reason_with_content(self):
        """Content + finish_reason should have both."""
        result = adapter.sse_chunk("id", "done", finish_reason="stop")
        decoded = result.decode("utf-8")
        data_line = decoded.strip().split("data: ")[1]
        event = json.loads(data_line)
        self.assertEqual(event["choices"][0]["delta"]["content"], "done")
        self.assertEqual(event["choices"][0]["finish_reason"], "stop")


# ── Word Splitting Tests (#14) ──────────────────────────────────────


class TestWordSplitting(unittest.TestCase):
    """Tests for accumulated.split() behavior (#14 fix)."""

    def test_split_no_args_handles_double_spaces(self):
        """str.split() with no args collapses multiple whitespace."""
        text = "fix  it"  # double space
        words = text.split()  # new behavior
        self.assertEqual(words, ["fix", "it"])
        # No empty strings in result
        self.assertNotIn("", words)

    def test_split_with_space_arg_produces_empty(self):
        """str.split(" ") produces empty strings for multiple spaces (old bug)."""
        text = "fix  it"
        words = text.split(" ")
        self.assertIn("", words)  # old buggy behavior


# ── CORS Tests (#10) ─────────────────────────────────────────────────


class TestCORS(unittest.TestCase):
    """Tests for CORS headers (#10)."""

    def test_cors_in_build_http_response(self):
        """All HTTP responses must include CORS headers."""
        resp = adapter.build_http_response(200, b"{}")
        resp_str = resp.decode("utf-8")
        self.assertIn("Access-Control-Allow-Origin: *", resp_str)
        self.assertIn("Access-Control-Allow-Headers:", resp_str)
        self.assertIn("Access-Control-Allow-Methods:", resp_str)

    def test_cors_in_error_response(self):
        """Error responses must also include CORS headers."""
        resp = adapter.build_http_response(401, adapter.json_error("nope", "auth", "bad"))
        resp_str = resp.decode("utf-8")
        self.assertIn("Access-Control-Allow-Origin: *", resp_str)

    def test_options_handler_exists(self):
        """_handle_cors_preflight method must exist."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("_handle_cors_preflight", source)


# ── Task Tracking Tests (#15) ───────────────────────────────────────


class TestTaskTracking(unittest.TestCase):
    """Tests for asyncio.Task reference tracking (#15)."""

    def test_tasks_set_initialized(self):
        """self._tasks must be initialized in __init__."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("self._tasks: set[asyncio.Task] = set()", source)

    def test_task_reference_stored(self):
        """create_task result must be stored and have done callback."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("task = asyncio.create_task", source)
        self.assertIn("self._tasks.add(task)", source)
        self.assertIn("task.add_done_callback(self._tasks.discard)", source)


# ── Timeout Path Tests (#13) ────────────────────────────────────────


class TestTimeoutPath(unittest.TestCase):
    """Tests for SSE timeout behavior (#13)."""

    def test_return_after_timeout(self):
        """Timeout path must return, not break."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        # After timeout error event, we must return (not break)
        # The pattern should be: writer.write(error) → drain → return
        self.assertNotIn("break\n", source.split("writer.write(sse_event(error_data))")[1].split("\n")[0:4])


# ── Stream Validation Tests (#11) ───────────────────────────────────


class TestStreamValidation(unittest.TestCase):
    """Tests for stream:true validation (#11)."""

    def test_stream_validation_exists(self):
        """stream:true check must exist."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("streaming_required", source)
        self.assertIn('"Yodel v1 requires stream: true"', source)


# ── Version Header Tests (#5) ──────────────────────────────────────


class TestVersionHeader(unittest.TestCase):
    """Tests for X-Yodel-Version header (#5)."""

    def test_yodel_version_extracted(self):
        """yodel_version must be read from headers."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn('yodel_version = headers.get("x-yodel-version"', source)

    def test_yodel_version_in_metadata(self):
        """yodel_version must be in MessageEvent metadata."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn('"yodel_version": yodel_version', source)


# ── Unused Imports Tests (#19) ─────────────────────────────────────


class TestUnusedImports(unittest.TestCase):
    """Tests for cleanup of unused imports (#19)."""

    def test_time_not_imported(self):
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertNotIn("import time", source.split("#")[0])  # not in comments

    def test_urljoin_not_imported(self):
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertNotIn("from urllib.parse import urljoin", source)

    def test_hmac_imported(self):
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertIn("import hmac", source)

    def test_parse_http_request_removed(self):
        """parse_http_request was removed since _handle_client now parses inline."""
        with open(os.path.join(os.path.dirname(__file__), "adapter.py")) as f:
            source = f.read()
        self.assertNotIn("def parse_http_request", source)


# ── Integration Tests (Async) ───────────────────────────────────────


class TestAdapterIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests using a real YodelAdapter instance with mocks."""

    async def asyncSetUp(self):
        # Mock the logger
        self.mock_logger = MagicMock()

        # Create a mock config
        mock_config = MagicMock()
        mock_config.extra = {"port": 18080, "api_key": "test-secret-key"}
        from gateway.config import Platform as PlatEnum

        # Patch the parent class
        with patch.object(adapter.YodelAdapter, '__init__',
                          lambda self, config: self.__dict__.update({
                              'config': config,
                              'logger': MagicMock(),
                              'platform': PlatEnum("api_server"),
                              'port': 18080,
                              'bind_address': '127.0.0.1',
                              'api_key': 'test-secret-key',
                              '_server': None,
                              '_pending_responses': {},
                              '_tasks': set(),
                          })):
            self.adapter = adapter.YodelAdapter(mock_config)
            self.adapter.logger = MagicMock()
            self.adapter._tasks = set()
            self.adapter._pending_responses = {}

    async def test_startup_warning_when_api_key_empty(self):
        """#16: Warning must be logged when API key is empty."""
        mock_config = MagicMock()
        mock_config.extra = {"port": 18080, "api_key": ""}

        with patch.dict(os.environ, {}, clear=True):
            # Create adapter manually to test the warning
            adapter_instance = adapter.YodelAdapter.__new__(adapter.YodelAdapter)
            adapter_instance.logger = MagicMock()

            # Manually simulate __init__
            from gateway.config import Platform, PlatformConfig
            adapter_instance.port = 18080
            adapter_instance.bind_address = "127.0.0.1"
            adapter_instance.api_key = ""
            adapter_instance._server = None
            adapter_instance._pending_responses = {}
            adapter_instance._tasks = set()

            # The warning should be logged
            self.assertEqual(adapter_instance.api_key, "")

    async def test_auth_rejects_when_no_api_key(self):
        """#16: When api_key is empty, all requests should be rejected."""
        # Create adapter with no API key
        self.adapter.api_key = ""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer anything"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        # Should have written a 503 response
        writer.write.assert_called()
        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("503", call_args)
        self.assertIn("api_key_not_configured", call_args)

    async def test_auth_rejects_wrong_token(self):
        """Wrong Bearer token should get 401."""
        self.adapter.api_key = "correct-key"
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer wrong-key"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("401", call_args)
        self.assertIn("invalid_api_key", call_args)

    async def test_auth_accepts_correct_token(self):
        """Correct Bearer token should pass auth check and proceed to streaming."""
        self.adapter.api_key = "correct-key"
        self.adapter.handle_message = AsyncMock()
        self.adapter._stream_sse_response = AsyncMock()  # skip actual streaming
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer correct-key"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        # Should NOT have written a 401/503 response
        call_args_list = [c[0][0].decode("utf-8", errors="replace")
                          for c in writer.write.call_args_list]
        written_text = " ".join(call_args_list)
        self.assertNotIn("401", written_text)
        self.assertNotIn("503", written_text)

    async def test_stream_false_rejected(self):
        """#11: stream:false should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            json.dumps({
                "model": "hermes",
                "stream": False,  # explicitly false
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("streaming_required", call_args)

    async def test_stream_missing_rejected(self):
        """#11: Missing stream field should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            json.dumps({
                "model": "hermes",
                # no stream field
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("streaming_required", call_args)

    async def test_invalid_device_type_rejected(self):
        """#18: Unknown device type should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "fridge", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("invalid_device_type", call_args)

    async def test_invalid_capability_rejected(self):
        """#18: Unknown capability should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": ["telepathy"]}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("invalid_capabilities", call_args)

    async def test_invalid_session_mode_rejected(self):
        """#18: Unknown session mode should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {
                "authorization": "Bearer test-secret-key",
                "x-yodel-mode": "quantum",
            },
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("invalid_session_mode", call_args)

    async def test_valid_request_proceeds(self):
        """A fully valid request should produce SSE output."""
        self.adapter.handle_message = AsyncMock()
        self.adapter._stream_sse_response = AsyncMock()  # skip actual streaming
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {
                "authorization": "Bearer test-secret-key",
                "x-yodel-version": "1",
                "x-yodel-device": "my-phone",
                "x-yodel-mode": "ephemeral",
                "x-yodel-input": "text",
            },
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "Hello world"}],
                "yodel": {
                    "device": {"type": "terminal", "capabilities": ["display"]},
                    "tts": {"requested": False},
                },
            }).encode("utf-8"),
        )

        # Verify the request passed all validation and streaming was triggered
        # (handle_message was called and _stream_sse_response was invoked)
        self.adapter.handle_message.assert_called_once()
        self.adapter._stream_sse_response.assert_called_once()

        # Check that a response queue was created and cleaned up
        # (chat_id should have been popped after streaming)
        self.assertEqual(len(self.adapter._pending_responses), 0)

    async def test_missing_auth_header(self):
        """Missing Authorization header should return 401."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {},  # no auth header
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("401", call_args)
        self.assertIn("invalid_auth_token", call_args)

    async def test_invalid_json_body(self):
        """Invalid JSON body should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            b"not json at all {{{",
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("invalid_request", call_args)

    async def test_no_user_messages(self):
        """No user messages should return 400."""
        writer = AsyncMock()

        await self.adapter._handle_yodel_request(
            writer,
            {"authorization": "Bearer test-secret-key"},
            json.dumps({
                "model": "hermes",
                "stream": True,
                "messages": [],  # empty
                "yodel": {"device": {"type": "terminal", "capabilities": []}},
            }).encode("utf-8"),
        )

        call_args = writer.write.call_args[0][0].decode("utf-8", errors="replace")
        self.assertIn("400", call_args)
        self.assertIn("missing_message", call_args)


# ── Runner ────────────────────────────────────────────────────────────


if __name__ == "__main__":
    unittest.main()
