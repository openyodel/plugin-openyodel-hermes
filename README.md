# Open Yodel × Hermes

Yodel protocol integration for [Hermes Agent](https://github.com/nousresearch/hermes-agent) — the open-source AI agent platform.

**Any Yodel-speaking device can now talk to Hermes.** No WIRE Gateway needed.

## What is this?

A [Hermes platform adapter plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters) that implements the [Yodel protocol](https://github.com/openyodel/spec) (v1.0-draft).

Yodel is the open communication protocol between AI clients and backends. Hermes is the AI agent. This repo connects them — one plugin, zero dependencies.

```
┌─────────────────────────────────────────────────────────┐
│  Yodel Devices                                          │
│  iOS · Android · Web PWA · Walkie-Talkie · Camera · IoT │
└──────────────┬──────────────────────────────────────────┘
               │  Yodel Protocol (SSE over HTTP)
               │  POST /v1/chat/completions
               ▼
┌──────────────────────────────────────────────────────────┐
│  openyodel-hermes (this plugin)                          │
│  · Yodel HTTP Server                                     │
│  · Header/body parsing                                   │
│  · Device capability injection                           │
│  · SSE response streaming                                │
└──────────────┬───────────────────────────────────────────┘
               │  Hermes MessageEvent
               ▼
┌──────────────────────────────────────────────────────────┐
│  Hermes Agent                                            │
│  · Provider routing (OpenAI, Anthropic, local)           │
│  · Skills · Cron · Memory · File I/O                     │
│  · Subagent delegation (PAI, Codex)                      │
│  · TTS (OpenAI, Edge, local)                             │
└──────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Install the plugin
./plugins/openyodel-hermes/install.sh --symlink

# 2. Configure Hermes
hermes config set YODEL_PORT 8080
hermes config set YODEL_API_KEY "$(openssl rand -base64 32)"

# 3. Enable the platform in config.yaml
# gateway:
#   platforms:
#     openyodel:
#       enabled: true

# 4. Restart Hermes

# 5. Test
python3 plugins/openyodel-hermes/test_client.py --health
python3 plugins/openyodel-hermes/test_client.py --key "your-key" --message "Hello!"
```

## Yodel Protocol Compliance

| Feature | Status |
|---------|--------|
| `POST /v1/chat/completions` | ✅ |
| `GET /v1/health` | ✅ |
| `GET /.well-known/yodel.json` | ✅ |
| `X-Yodel-Version`, `X-Yodel-Device`, `X-Yodel-Agent` | ✅ |
| `X-Yodel-Mode` (ephemeral/persistent) | ✅ |
| `X-Yodel-Input` (voice/text) | ✅ |
| `X-Yodel-Session` | ✅ |
| `yodel.device` (type + capabilities) | ✅ |
| `yodel.tts` (requested, voice, provider, format) | ✅ |
| `yodel.input_lang` (BCP 47) | ✅ |
| SSE streaming (`text/event-stream`) | ✅ |
| OpenAI-compatible chunk format | ✅ |
| `[DONE]` stream termination | ✅ |
| Bearer token authentication | ✅ |
| mDNS/DNS-SD discovery | 🔲 Future |

## Device Capabilities → Hermes Context

| Yodel Capability | Hermes Behavior |
|-----------------|-----------------|
| `audio_out` | Response includes TTS audio URL |
| `audio_in` | Expects voice input (STT done on device) |
| `display` | Rich formatting (tables, markdown, code blocks) |
| `haptic` | Haptic-friendly response style |
| `camera` | Expects image analysis requests |
| `cron` (custom) | Device can receive scheduled messages |
| `skills` (custom) | Device can invoke Hermes skills |

Custom capabilities use namespacing: `hermes:skills`, `hermes:cron`, etc.

- [ADR-002](decisions/ADR-002-implementation-decisions.md) — HTTP server, correlation, SSE, auth choices

## Repository Structure

```
openyodel/
├── README.md                           # This file
├── decisions/                          # Architecture Decision Records
│   ├── ADR-001-yodel-platform-adapter.md
│   └── ADR-002-implementation-decisions.md
├── docs/                               # Extended documentation
│   ├── device-scenarios.md             # Walkie-talkie, camera, printer examples
│   └── configuration.md                # Full configuration reference
└── plugins/
    └── openyodel-hermes/               # Hermes plugin
        ├── plugin.yaml                 # Plugin metadata
        ├── adapter.py                  # Yodel HTTP server + adapter
        ├── test_client.py              # Test client
        ├── install.sh                  # Setup script
        └── README.md                   # Plugin README
```

## Related Projects

- [Yodel Protocol Spec](https://github.com/openyodel/spec) — The open protocol
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) — The AI agent platform

## License

MIT — see [LICENSE](LICENSE) file.
