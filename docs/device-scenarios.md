# Device Scenarios

How different Yodel-speaking devices interact with Hermes through the openyodel adapter.

## Walkie-Talkie App (Voice-First)

A smartphone app that does on-device STT and TTS. Sends transcribed text to Hermes, plays audio responses.

**Device profile:**
```json
{
  "type": "ios",
  "capabilities": ["audio_out", "audio_in", "haptic"]
}
```

**Flow:**
```
1. User presses PTT button → app records audio
2. App runs on-device STT → "What's the weather in Tokyo?"
3. App sends Yodel request:
   POST /v1/chat/completions
   X-Yodel-Input: voice
   X-Yodel-Mode: ephemeral
   { "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
     "yodel": {
       "tts": {"requested": true, "voice": "alloy", "format": "opus"},
       "device": {"type": "ios", "capabilities": ["audio_out", "audio_in", "haptic"]}
     }
   }
4. Hermes receives: "[Device: ios, capabilities: audio_out, audio_in, haptic]
   [Input mode: voice — user spoke this message]
   What's the weather in Tokyo?"
5. Hermes responds concisely (voice-friendly), TTS audio URL included
6. App plays audio response
```

**Hermes adaptation:**
- Keeps responses short (no long tables, no code blocks)
- Uses `yodel.tts_url` for audio playback
- Can delegate complex queries to PAI via subagent

---

## Smart Camera

A camera that sends images for analysis. Might have a small display for text responses.

**Device profile:**
```json
{
  "type": "embedded",
  "capabilities": ["camera", "display"]
}
```

**Flow:**
```
1. Camera captures image
2. Sends Yodel request with multi-modal content:
   POST /v1/chat/completions
   X-Yodel-Device: cam-001
   { "messages": [{
       "role": "user",
       "content": [
         {"type": "text", "text": "What's in this image?"},
         {"type": "image_url", "image_url": {"url": "https://..."}}
       ]
     }],
     "yodel": {
       "device": {"type": "embedded", "capabilities": ["camera", "display"]}
     }
   }
3. Hermes processes with vision model
4. Response: "A red bicycle leaning against a brick wall..."
5. Camera shows text on display
```

**Hermes adaptation:**
- Uses vision-capable provider (GPT-4V, Claude Vision)
- Can trigger cron job: "Take a photo every hour and check if the package arrived"
- Can delegate complex analysis to PAI

---

## Cloud Storage Trigger

A cloud storage service (Nextcloud, S3) that sends events when files change. Hermes processes the files.

**Device profile:**
```json
{
  "type": "agent_platform",
  "capabilities": ["yodel_endpoint", "hermes:skills"]
}
```

**Flow:**
```
1. File uploaded to cloud storage
2. Webhook fires Yodel request:
   POST /v1/chat/completions
   X-Yodel-Agent: file-processor
   { "messages": [{
       "role": "user",
       "content": "New file uploaded: invoices/2026-06.pdf. Process it."
     }],
     "yodel": {
       "device": {"type": "agent_platform", "capabilities": ["hermes:skills"]}
     }
   }
3. Hermes uses file tools to read the PDF
4. Hermes invokes invoice-processing skill
5. Extracts data, saves to database
6. Sends confirmation back via SSE
```

**Hermes adaptation:**
- Agent platform capability → Hermes treats it as automated, not human
- Can invoke skills, cron jobs, PAI delegation
- Response contains structured data, not conversational text

---

## IoT Printer

A thermal printer that receives formatted output. No input capabilities.

**Device profile:**
```json
{
  "type": "embedded",
  "capabilities": ["display"]
}
```

**Flow:**
```
1. Cron job triggers: "Print daily summary"
2. Hermes generates formatted text with print-friendly width
3. Sends Yodel response (text only, no markdown)
4. Printer outputs: "=== Daily Summary === ..."
```

**Hermes adaptation:**
- No `audio_out` → no TTS
- `display` only → plain text, 40-char width, no formatting
- Cron integration for scheduled prints

---

## Custom Capability Namespacing

Devices can declare custom capabilities with the `hermes:` prefix:

| Capability | Meaning |
|-----------|---------|
| `hermes:skills` | Device can trigger Hermes skills |
| `hermes:cron` | Device can receive scheduled messages |
| `hermes:delegation` | Device can trigger subagent delegation |
| `hermes:memory` | Device has access to Hermes memory |
