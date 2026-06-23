# ADR-002: Implementation Decisions for Yodel Adapter

**Status:** Accepted  
**Date:** 2026-06-23  
**Author:** Roland Strahlhofer (via Hermes Agent)

## Context

Der Yodel Platform Adapter muss mehrere technische Entscheidungen treffen:
HTTP-Server-Implementierung, Request-Response-Korrelation, SSE-Streaming-Strategie,
und Device-Kontext-Injektion.

## Decisions

### 1. HTTP Server: `asyncio.start_server` (stdlib, keine Dependencies)

**Decision:** Built-in `asyncio.start_server` statt aiohttp/FastAPI/Starlette.

**Rationale:**
- Hermes läuft bereits auf asyncio — keine zweite Event-Loop
- Keine externen Dependencies (der Adapter ist ein Drop-in-Plugin)
- Der Yodel-Endpoint ist ein Single-Purpose-HTTP-Server (3 Routen)
- ~50 Zeilen HTTP-Parsing vs. Framework-Overhead

**Trade-off:** Kein automatisches Routing, kein Middleware-System. Akzeptabel für 3 Endpoints.

### 2. Request-Response-Korrelation: `chat_id` + `asyncio.Queue`

**Decision:** Jeder Yodel-Request bekommt eine unique `chat_id` (`{device_id}:{random_suffix}`). Die Hermes-Antwort kommt via `send(chat_id, content)` zurück. Eine `asyncio.Queue` pro `chat_id` korreliert Request und Response.

**Rationale:**
- Hermes' Platform-Adapter-Pattern ist Push-basiert (Message → Agent → `send()`)
- Yodel ist Request-Response (Client wartet auf SSE-Stream)
- Die `chat_id` ist der natürliche Korrelationsschlüssel im Hermes-Modell
- `asyncio.Queue` ist threadsafe und unterstützt Timeout

**Trade-off:** Der `chat_id` muss pro Request unique sein (nicht pro Device). Parallele Requests vom selben Device kollidieren sonst.

### 3. SSE-Streaming: Akkumulieren, dann chunked senden

**Decision:** V1 akkumuliert die vollständige Hermes-Antwort und streamt sie dann wortweise als SSE-Chunks an den Client.

**Rationale:**
- Hermes' `send()` liefert aktuell die komplette Antwort (kein Streaming-Callback)
- Wortweises Senden simuliert Streaming für den Client
- OpenAI-kompatible `chat.completion.chunk` Events

**Future:** V2 sollte echte Token-by-Token-Streams von Hermes' Provider-Layer abgreifen.

### 4. Device-Kontext: Text-Injektion in User-Message

**Decision:** Device-Typ und Capabilities werden als Text-Präfix in die User-Message injected.

**Rationale:**
- Einfachste Integration ohne Hermes-Core-Änderungen
- Hermes' System-Prompt kann darauf reagieren
- Alle Skills/Subagents sehen den Device-Kontext automatisch

**Trade-off:** Nicht so clean wie ein dediziertes Metadaten-Feld. Polluted die User-Message.

### 5. Auth: Simple Bearer Token

**Decision:** Ein statischer API-Key (`YODEL_API_KEY`) authentifiziert alle Yodel-Devices.

**Rationale:**
- V1: Einfachster Start
- Hermes hat bereits ein ausgereiftes Berechtigungssystem pro Plattform
- Device-spezifische Secrets (wie WIRE sie hat) können später ergänzt werden

**Future:** Device-Secrets mit SHA-256-Hashing, wie in WIRE implementiert.

## Consequences

- Der Adapter ist zero-dependency und <500 Zeilen
- SSE-Streaming ist simuliert, nicht echt — für die meisten Use-Cases ausreichend
- Device-Kontext ist sichtbar in der User-Message (kann im System-Prompt gefiltert werden)
- Auth ist einfach — reicht für private/tailscale deployments

## References

- [ADR-001: Yodel Platform Adapter](ADR-001-yodel-platform-adapter.md)
- [Implementation: `plugins/openyodel-hermes/adapter.py`](../plugins/openyodel-hermes/adapter.py)
- [Yodel Protocol Spec §6: Request Format](https://github.com/openyodel/spec/blob/main/v1/spec.md#6-request-format)
