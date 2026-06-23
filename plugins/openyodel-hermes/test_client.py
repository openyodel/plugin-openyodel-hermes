#!/usr/bin/env python3
"""
Test client for the Yodel Hermes adapter.

Sends a Yodel-compatible request and prints the SSE stream.
Usage:
    python3 test_client.py [--port 8080] [--host localhost] [--key your-key]
"""

import argparse
import json
import sys
import urllib.request
import urllib.error


def send_yodel_request(host: str, port: int, api_key: str, message: str,
                       agent: str = "", device_id: str = "",
                       device_type: str = "terminal",
                       capabilities: list[str] | None = None,
                       input_mode: str = "text",
                       input_lang: str = "") -> None:
    """Send a Yodel request and print the SSE stream."""
    url = f"http://{host}:{port}/v1/chat/completions"

    body = {
        "model": "hermes",
        "stream": True,
        "messages": [
            {"role": "user", "content": message}
        ],
        "yodel": {
            "input_lang": input_lang,
            "tts": {
                "requested": False,
                "voice": "",
                "format": "opus",
            },
            "device": {
                "type": device_type,
                "capabilities": capabilities or [],
            },
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Yodel-Version": "1",
    }
    if device_id:
        headers["X-Yodel-Device"] = device_id
    if agent:
        headers["X-Yodel-Agent"] = agent
    headers["X-Yodel-Mode"] = "ephemeral"
    headers["X-Yodel-Input"] = input_mode

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    print(f"→ Sending to {url}")
    print(f"  Device: {device_type}, Capabilities: {capabilities}")
    print(f"  Input: {input_mode}, Message: {message[:80]}...")
    print()

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            content_type = response.headers.get("Content-Type", "")
            print(f"← Status: {response.status}")
            print(f"← Content-Type: {content_type}")
            print()

            if "text/event-stream" not in content_type:
                body_text = response.read().decode("utf-8", errors="replace")
                print(f"← Error body: {body_text}")
                return

            # Read SSE stream
            buffer = ""
            while True:
                chunk = response.read(1024)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    for event_line in line.split("\n"):
                        if event_line.startswith("data: "):
                            data_str = event_line[6:]
                            if data_str == "[DONE]":
                                print("← [DONE] — stream complete")
                                return
                            try:
                                parsed = json.loads(data_str)
                                # Handle yodel event
                                if "yodel" in parsed:
                                    print(f"← YODEL: {json.dumps(parsed['yodel'], indent=2)}")
                                    continue
                                # Handle error in stream
                                if "error" in parsed:
                                    print(f"← STREAM ERROR: {parsed['error']}")
                                    continue
                                # Handle content chunk
                                choices = parsed.get("choices", [])
                                for c in choices:
                                    delta = c.get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        print(content, end="", flush=True)
                                    finish = c.get("finish_reason")
                                    if finish:
                                        print(f"\n← finish_reason: {finish}")
                            except json.JSONDecodeError:
                                print(f"← RAW: {data_str}")

    except urllib.error.HTTPError as e:
        print(f"← HTTP Error {e.code}: {e.reason}")
        try:
            body = e.read().decode("utf-8")
            print(f"← Body: {body}")
        except Exception:
            pass
    except urllib.error.URLError as e:
        print(f"← Connection error: {e.reason}")
    except KeyboardInterrupt:
        print("\n← Interrupted")


def check_health(host: str, port: int) -> None:
    """Check the health endpoint."""
    url = f"http://{host}:{port}/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✓ Health: {json.dumps(data, indent=2)}")
    except Exception as e:
        print(f"✗ Health check failed: {e}")


def check_discovery(host: str, port: int) -> None:
    """Check the well-known endpoint."""
    url = f"http://{host}:{port}/.well-known/yodel.json"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✓ Discovery: {json.dumps(data, indent=2)}")
    except Exception as e:
        print(f"✗ Discovery failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Yodel Hermes Test Client")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--key", default="test-key")
    parser.add_argument("--message", default="Hello! What can you do?")
    parser.add_argument("--agent", default="")
    parser.add_argument("--device-id", default="test-device-001")
    parser.add_argument("--device-type", default="terminal",
                        choices=["ios", "android", "web", "car", "speaker",
                                 "terminal", "embedded", "agent_platform"])
    parser.add_argument("--capabilities", nargs="*",
                        default=["display", "audio_out"])
    parser.add_argument("--input-mode", default="text",
                        choices=["text", "voice"])
    parser.add_argument("--lang", default="")
    parser.add_argument("--health", action="store_true",
                        help="Only check health endpoint")
    parser.add_argument("--discovery", action="store_true",
                        help="Only check discovery endpoint")
    args = parser.parse_args()

    if args.health:
        check_health(args.host, args.port)
        return

    if args.discovery:
        check_discovery(args.host, args.port)
        return

    send_yodel_request(
        host=args.host,
        port=args.port,
        api_key=args.key,
        message=args.message,
        agent=args.agent,
        device_id=args.device_id,
        device_type=args.device_type,
        capabilities=args.capabilities,
        input_mode=args.input_mode,
        input_lang=args.lang,
    )


if __name__ == "__main__":
    main()
