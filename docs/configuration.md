# Configuration Reference

Full configuration guide for the openyodel-hermes plugin.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `YODEL_PORT` | **Yes** | — | Port for the Yodel HTTP server |
| `YODEL_API_KEY` | **Yes** | — | Bearer token for device authentication |
| `YODEL_BIND_ADDRESS` | No | `0.0.0.0` | Network interface to bind to |
| `YODEL_HOME_CHANNEL` | No | — | Default channel for cron delivery via Yodel |

## config.yaml

```yaml
gateway:
  platforms:
    openyodel:
      enabled: true
      extra:
        port: 8080           # Override YODEL_PORT
        api_key: "sk-..."    # Override YODEL_API_KEY
        bind_address: "127.0.0.1"  # localhost only
```

## CLI Setup

```bash
# Required
hermes config set YODEL_PORT 8080
hermes config set YODEL_API_KEY "your-secret-token"

# Optional
hermes config set YODEL_BIND_ADDRESS 127.0.0.1
hermes config set YODEL_HOME_CHANNEL "yodel-home"
```

## Security

### Token Generation

```bash
# Generate a secure random API key
openssl rand -base64 32
# Output: dGhpcyBpcyBhIHNlY3VyZSByYW5kb20gdG9rZW4gZm9yIHlvZGVs...
```

### Network Security

- **Localhost only**: Set `YODEL_BIND_ADDRESS=127.0.0.1` for local development
- **Tailscale/WireGuard**: Set `YODEL_BIND_ADDRESS=0.0.0.0` and use VPN for secure transport
- **Public internet**: Put Hermes behind a reverse proxy (nginx, Caddy) with TLS
- **Plain HTTP**: Yodel spec requires TLS for public networks. Use Tailscale or a reverse proxy for encryption.

### Device Authentication

Currently, all devices share one API key. For multi-device setups:

1. **One key per device group**: Create separate Hermes profiles with different keys
2. **Future**: Device-specific secrets with SHA-256 hashing (like WIRE)

## Device Routing

### By X-Yodel-Agent Header

The `X-Yodel-Agent` header is passed as metadata. Configure routing in Hermes:

```yaml
# Route different agents to different skills/providers
gateway:
  routing:
    - agent: cooking
      skill: cooking-assistant
    - agent: coding
      provider: anthropic
      skill: code-review
    - agent: default
      provider: openai
```

### By Device Type

Device capabilities are injected into the message context. The agent adapts automatically:

- `audio_out` → concise responses, TTS audio
- `display` → rich formatting, tables, code blocks
- `camera` → vision model selection

## TTS Configuration

TTS is handled by Hermes' existing TTS system. The Yodel adapter passes TTS preferences:

```json
{
  "yodel": {
    "tts": {
      "requested": true,
      "voice": "alloy",
      "format": "opus"
    }
  }
}
```

Hermes routes TTS through its configured provider (OpenAI TTS, Edge TTS, or local).

## Troubleshooting

### Plugin not loading

```bash
# Check plugin directory
ls ~/.hermes/plugins/openyodel-hermes/

# Check Hermes logs for registration errors
hermes logs | grep openyodel
```

### Port already in use

```bash
# Check what's using the port
lsof -i :8080

# Use a different port
hermes config set YODEL_PORT 8081
```

### Test the endpoint

```bash
# Health check
curl http://localhost:8080/v1/health

# Discovery
curl http://localhost:8080/.well-known/yodel.json

# Full Yodel request
python3 plugins/openyodel-hermes/test_client.py \
  --key "your-key" \
  --message "Hello" \
  --device-type ios \
  --capabilities display audio_out
```
