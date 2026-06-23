# Open Yodel → Hermes Platform Adapter

Yodel protocol platform adapter for Hermes Agent.

## What it does

Starts a Yodel-compatible HTTP endpoint inside Hermes. Any device that speaks
the [Yodel protocol](https://github.com/openyodel/spec) can connect directly:

```
Device (iOS, Android, PWA, IoT)
    │  POST /v1/chat/completions
    │  X-Yodel-Device, X-Yodel-Agent, X-Yodel-Mode, ...
    │  { "model": "...", "stream": true, "messages": [...], "yodel": {...} }
    ▼
Hermes (Yodel Adapter)
    │  Converts Yodel → Hermes MessageEvent
    │  Injects device capabilities into context
    │  Agent processes, delegates to PAI/Skills
    │  Streams SSE response back
    ▼
Device (receives text + optional TTS audio)
```

## Setup

```bash
# Copy plugin to Hermes plugins directory
cp -r plugins/openyodel-hermes ~/.hermes/plugins/

# Or symlink for development
ln -s $(pwd)/plugins/openyodel-hermes ~/.hermes/plugins/

# Configure
hermes config set YODEL_PORT 8080
hermes config set YODEL_API_KEY "your-secret-key"
hermes config set YODEL_BIND_ADDRESS 0.0.0.0  # optional

# Enable in config.yaml
# gateway:
#   platforms:
#     openyodel:
#       enabled: true
```

## Yodel Protocol Support

| Feature | Status |
|---------|--------|
| POST /v1/chat/completions | ✅ |
| GET /v1/health | ✅ |
| GET /.well-known/yodel.json | ✅ |
| X-Yodel-Version | ✅ |
| X-Yodel-Device | ✅ |
| X-Yodel-Agent | ✅ |
| X-Yodel-Mode | ✅ |
| X-Yodel-Input | ✅ |
| X-Yodel-Session | ✅ |
| yodel.device (type + capabilities) | ✅ |
| yodel.tts | ✅ (metadata, TTS handled by Hermes) |
| yodel.input_lang | ✅ |
| SSE streaming | ✅ |
| Bearer token auth | ✅ |
| mDNS discovery | 🔲 (future) |

## Device Capabilities → Hermes Context

When a Yodel device connects, its capabilities are injected into the system prompt
context so Hermes can adapt its responses:

- `audio_out` → Include TTS audio URLs
- `audio_in` → Expect voice input (STT already done on device)
- `display` → Rich formatting (tables, markdown)
- `haptic` → Include haptic feedback signals
- `camera` → Expect image analysis requests

## Architecture Decision

**No WIRE Gateway needed.** Hermes is the gateway. The Yodel adapter replaces
WIRE's device registration, agent config, and proxy functionality. Hermes already
has:

- Provider routing (agent model selection)
- API key management (encrypted at rest)
- Skill/subagent system (agent capabilities)
- TTS integration
- Cron scheduling
- Platform management dashboard
