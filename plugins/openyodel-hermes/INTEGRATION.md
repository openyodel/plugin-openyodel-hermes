# Integration Guide

> Für Entwickler, die eine App, ein SDK oder einen Client für das
> openyodel-hermes Plugin bauen wollen. Plattformneutral — iOS, Android,
> Web, Embedded, CLI, etc.

---

## 1. Verbindung

### 1.1 Endpoint

```
POST http://<host>:<port>/v1/chat/completions
```

- Default-Port: `8080` (konfigurierbar via `YODEL_PORT`)
- HTTPS wird empfohlen (Reverse-Proxy / Tailscale / Cloudflare Tunnel)
- CORS-Headers sind gesetzt — Web/PWA-Clients funktionieren ohne Proxy

### 1.2 Health-Check

```
GET /v1/health
→ 200 {"status":"ok","version":"1.0-draft","gateway":"hermes"}
```

Vor dem ersten Request aufrufen, um Erreichbarkeit zu prüfen.

### 1.3 Discovery (optional)

```
GET /.well-known/yodel.json
→ 200 {"yodel_version":1,"gateway":"hermes","endpoints":{...},"capabilities":[...],"agents":[]}
```

Kann in Clients für Auto-Konfiguration genutzt werden.

### 1.4 Authentifizierung

Jeder Request braucht:
```
Authorization: Bearer <api_key>
```

- Der Key wird serverseitig konfiguriert (`YODEL_API_KEY`)
- Kein OAuth, kein Token-Refresh — statischer Bearer-Token
- Fehlt der Key serverseitig, antwortet der Server mit HTTP 503
- Key-Vergleich erfolgt constant-time (`hmac.compare_digest`)
- **Empfehlung:** Key in Secure Storage ablegen (iOS Keychain, Android Keystore, Web: nie im localStorage)

---

## 2. Request

### 2.1 HTTP-Headers

| Header | Pflicht | Typ | Beschreibung |
|--------|---------|-----|-------------|
| `Authorization` | ✅ | `Bearer <key>` | API-Key |
| `Content-Type` | ✅ | `application/json` | |
| `X-Yodel-Version` | empfohlen | `"1"` | Protokoll-Version |
| `X-Yodel-Device` | empfohlen | string | Eindeutige Device-ID (z.B. UUID) |
| `X-Yodel-Agent` | optional | string | Agent-Slug (für Multi-Agent-Setups) |
| `X-Yodel-Mode` | optional | `"ephemeral"` / `"persistent"` | Sitzungsmodus (default: ephemeral) |
| `X-Yodel-Input` | empfohlen | `"text"` / `"voice"` | Input-Modalität |
| `X-Yodel-Session` | optional | string | Session-ID (für persistente Sitzungen) |

### 2.2 JSON-Body

```json
{
  "model": "hermes",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "Hello, what can you do?"
    }
  ],
  "yodel": {
    "input_lang": "de",
    "tts": {
      "requested": false,
      "voice": "",
      "provider": "",
      "format": "opus"
    },
    "device": {
      "type": "ios",
      "capabilities": ["audio_out", "audio_in", "display"]
    }
  }
}
```

### 2.3 Feld-Details

**`model`** — Immer `"hermes"`. Der Name des LLM-Modells auf Hermes-Seite.

**`stream`** — **MUSS `true` sein.** Der Adapter lehnt `false` oder fehlendes Feld mit HTTP 400 ab.

**`messages`** — OpenAI-kompatibles Array. Der Adapter nimmt die **letzte** User-Nachricht.
- `role`: `"user"` (auch `"system"` und `"assistant"` werden toleriert aber ignoriert)
- `content`: string (transkribierter Text) oder Array von Content-Parts (multimodal)

**`yodel.input_lang`** — BCP-47-Sprachcode, z.B. `"de"`, `"en"`, `"fr"`. Wird als Kontext an Hermes durchgereicht.

**`yodel.tts`** — TTS-Konfiguration:
- `requested`: `false` → Server antwortet mit Text (Client macht TTS selbst)
- `requested`: `true` → Server kann Audio-URL im SSE-Stream mitsenden (aktuell nicht implementiert)
- `voice`: Name der Stimme (z.B. `"alloy"`)
- `provider`: TTS-Provider (z.B. `"openai"`, `"elevenlabs"`)
- `format`: Audio-Format (z.B. `"opus"`, `"mp3"`)

**`yodel.device`** — Device-Info:
- `type`: Einer von: `ios`, `android`, `web`, `car`, `speaker`, `terminal`, `embedded`, `agent_platform`
- `capabilities`: Array aus: `audio_out`, `audio_in`, `display`, `haptic`, `camera`

**Validierung:** Ungültige Werte für `type`, `capabilities` oder `mode` werden mit HTTP 400 abgelehnt (inkl. Liste der gültigen Werte).

### 2.4 Multimodale Requests

`content` kann ein Array von Content-Parts sein:
```json
"content": [
  {"type": "text", "text": "Was siehst du auf diesem Bild?"},
  {"type": "image_url", "image_url": {"url": "https://..."}}
]
```

Unterstützte Typen: `text`, `image_url`, `image`, `audio`.
Der Adapter extrahiert Text-Parts und vermerkt Medien-Anhänge.

---

## 3. Response (SSE-Stream)

Der Server antwortet mit:
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
Access-Control-Allow-Origin: *
```

### 3.1 Chunk-Typen

Es gibt 5 Event-Typen im Stream:

**A) Role-Chunk** — genau einmal am Anfang:
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"hermes","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}
```

**B) Content-Chunks** — beliebig viele:
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"hermes","choices":[{"index":0,"delta":{"content":"Das Wetter"},"finish_reason":null}]}
```

**C) Finish-Chunk** — genau einmal am Ende:
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","model":"hermes","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
```

**D) Yodel-Event** — einmal nach Finish:
```
data: {"yodel":{"session_id":"device-abc:1a2b3c4d"}}
```

**E) Stream-Ende:**
```
data: [DONE]
```

### 3.2 SSE-Parsing (Pseudocode)

```
buffer = ""
for each line in stream:
    if line starts with "data: ":
        payload = line[6:]
        if payload == "[DONE]":
            break
        event = json.parse(payload)
        if "yodel" in event:
            session_id = event.yodel.session_id
        elif "error" in event:
            handle_error(event.error)
        elif "choices" in event:
            delta = event.choices[0].delta
            if "content" in delta:
                append_to_response(delta.content)
            if event.choices[0].finish_reason:
                response_complete()
```

### 3.3 Wichtige Parsing-Regeln

- **`data: [DONE]` nicht als JSON parsen** — es ist ein plain string
- **Finish-Chunk hat leeren Delta** (`{}`), kein `role`-Feld
- **Content-Chunks haben NUR `content`**, kein `role`
- **Nur `data:`-Zeilen** verarbeiten; Kommentare (`:`) und Leerzeilen ignorieren
- **`\n\n`** (Doppel-Newline) trennt Events

### 3.4 Fehler im Stream

Der Server kann mitten im Stream einen Fehler senden:
```
data: {"error":{"message":"Response timed out","type":"backend_error","code":"timeout"}}
```

Danach wird der Stream geschlossen. Kein `[DONE]`, kein Finish-Chunk.

---

## 4. Fehlercodes

### HTTP-Fehler (vor Stream-Beginn)

| HTTP | Code | Bedeutung | Client-Reaktion |
|------|------|-----------|----------------|
| 400 | `invalid_request` | JSON-Syntaxfehler | Body prüfen, Retry |
| 400 | `streaming_required` | `stream: false` oder fehlt | Immer `stream: true` setzen |
| 400 | `invalid_device_type` | Unbekannter `device.type` | Gültigen Typ verwenden |
| 400 | `invalid_capabilities` | Unbekannte Capability | Nur bekannte Werte senden |
| 400 | `invalid_session_mode` | Unbekannter `mode` | `ephemeral` oder `persistent` |
| 400 | `invalid_input_mode` | Unbekannter `X-Yodel-Input` | `text` oder `voice` |
| 400 | `missing_message` | Keine User-Nachricht | Mindestens eine User-Message |
| 401 | `invalid_auth_token` | Fehlender/falscher Header | `Authorization: Bearer <key>` |
| 401 | `invalid_api_key` | Falscher Key | Key prüfen |
| 413 | `body_too_large` | Request > 4 MB | Body verkleinern |
| 503 | `api_key_not_configured` | Server nicht konfiguriert | Admin kontaktieren |

### Stream-Fehler (im SSE-Stream)

| Code | Bedeutung | Client-Reaktion |
|------|-----------|----------------|
| `timeout` | Hermes antwortet nicht in 120s | Retry mit Backoff, gleiche Session-ID |

---

## 5. Session-Handling

**Ephemeral (default):**
- Jeder Request ist unabhängig
- Keine Konversationshistorie serverseitig
- Session-ID wird trotzdem im Yodel-Event zurückgegeben (für Logging)

**Persistent (optional, in Entwicklung):**
- `X-Yodel-Session`-Header im Request mitsenden
- Gleiche Session-ID über mehrere Requests
- Server kann Kontext serverseitig halten (Zukunft)

**Empfehlung für MVP:** Ephemeral mit Client-seitigem Nachrichtenverlauf.
Der Client sendet bei Bedarf die letzten N Nachrichten als `messages`-Array mit.

---

## 6. TTS-Strategien

### Variante A: On-Device TTS (empfohlen)

```
Client                           Server
  │                                │
  │  tts.requested: false          │
  │  stream: true                  │
  │──────────────────────────────→│
  │                                │
  │  SSE: {"delta":{"content":…}}  │
  │←──────────────────────────────│
  │                                │
  │  Lokales TTS (AVSpeechSynth,  │
  │  Android TTS, Web Speech API) │
```

- Null Latenz für erste gesprochene Worte
- Keine Server-Kosten für Audio-Generierung
- Funktioniert offline (nach Empfang des Textes)

### Variante B: Server-TTS (Zukunft)

```
Client                           Server
  │                                │
  │  tts.requested: true           │
  │  tts.voice: "alloy"            │
  │  tts.format: "opus"            │
  │──────────────────────────────→│
  │                                │
  │  SSE: {"tts_audio":{"url":…}}  │  (noch nicht implementiert)
  │←──────────────────────────────│
  │                                │
  │  Audio-Download + Wiedergabe   │
```

---

## 7. Best Practices

### Timeout-Handling
- Server hat 120s Timeout für die LLM-Antwort
- Client sollte eigenes Timeout setzen (empfohlen: 130s)
- Bei Timeout: Request mit gleicher Session-ID wiederholen

### Retry & Backoff
- Exponentielles Backoff bei 5xx-Fehlern: 1s → 2s → 4s → 8s (max 60s)
- Bei 4xx-Fehlern NICHT wiederholen (Fehler im Request)
- Bei Netzwerkfehlern: lineares Retry (3 Versuche, 2s Pause)

### Caching
- Health-Check-Ergebnis 60s cachen
- Well-Known-Ergebnis 3600s cachen
- API-Key nie cachen (Secure Storage)

### Security
- API-Key in iOS Keychain / Android Keystore / Web: sessionStorage (nie localStorage)
- HTTPS verwenden (Reverse-Proxy oder Tailscale)
- Kein Logging von API-Key oder User-Input im Production-Build

### Streaming
- Stream-Verbindung nicht im Hauptthread blockieren
- Bei Abbruch (User drückt erneut PTT): alte Verbindung schließen, neue aufbauen
- `[DONE]`-Event als einziges Signal für Stream-Ende behandeln

---

## 8. Vollständiges Request-Beispiel (curl)

```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -H "X-Yodel-Version: 1" \
  -H "X-Yodel-Device: my-device-001" \
  -H "X-Yodel-Input: voice" \
  -H "X-Yodel-Mode: ephemeral" \
  -d '{
    "model": "hermes",
    "stream": true,
    "messages": [
      {"role": "user", "content": "Wie wird das Wetter morgen?"}
    ],
    "yodel": {
      "input_lang": "de",
      "tts": {
        "requested": false,
        "voice": "",
        "provider": "",
        "format": "opus"
      },
      "device": {
        "type": "ios",
        "capabilities": ["audio_out", "audio_in", "display"]
      }
    }
  }'
```

---

## 9. Referenzen

- [Open Yodel Spec](https://github.com/openyodel/spec)
- [Plugin-Repo](https://github.com/openyodel/plugin-openyodel-hermes)
- [iOS Referenz-Client (Swift)](https://github.com/moongrabber/wire-walkie-swift)
- [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs)

---

*Dokumentversion: 1.0 — 2026-06-26*
*Plugin-Version: v1.0.0-draft*
