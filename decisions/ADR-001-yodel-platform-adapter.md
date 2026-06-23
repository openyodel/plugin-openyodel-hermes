# ADR-001: Yodel Platform Adapter statt WIRE Gateway Middleware

**Status:** Accepted  
**Date:** 2026-06-23  
**Author:** Roland Strahlhofer (via Hermes Agent)

## Context

Wir haben zwei Systeme, die beide als "Gateway" fungieren:

1. **WIRE Gateway** (`moongrabber/wire-gateway`) — Proprietäres Gateway für das Yodel-Protokoll. Bietet Device-Registrierung, Agent-Konfiguration, Device-Agent-Bindings und Yodel-Proxy mit API-Key-Injection.

2. **Hermes Agent** (`nousresearch/hermes-agent`) — Open-Source AI-Agent-Plattform. Bietet Multi-Platform-Support (Telegram, Discord, SMS, API), Provider-Routing, Subagent-Delegation, Skills, Cron, Memory, File-IO und TTS.

WIRE wurde als dediziertes Gateway für Yodel-fähige Devices gebaut. Hermes ist ein vollständiger AI-Agent.

## Decision

**Yodel wird als Hermes Platform Adapter implementiert, nicht als separates WIRE Gateway.**

Das Yodel-Protokoll wird über einen Hermes-Plugin-Adapter (`openyodel-hermes`) nativ unterstützt. Kein WIRE Gateway, kein WIRE Dashboard, keine Middleware-Schicht.

## Rationale

### Funktionale Überlappung (WIRE ist redundant)

| WIRE-Feature | Hermes-Äquivalent |
|-------------|-------------------|
| Device-Registrierung + Secret-Management | Plattform-Authentifizierung via API-Key |
| Agent-CRUD (Slug, Endpoint, Model, Prompt) | Provider-Config + Skills |
| Device-Agent-Bindings (Soft-Default) | Skill-Berechtigungen + Plattform-Routing |
| Yodel-Proxy (API-Key-Injection) | Provider-Layer (existiert seit v1) |
| Config-Pull für Devices | Entfällt — Device sendet Capabilities pro Request |
| Audio-Cache mit TTL/LRU | Hermes TTS (ausbaubar) |
| Dashboard (Devices, Agents, Bindings) | Hermes Dashboard + CLI |
| Request-Logging | Hermes Session-Search + Logging |

### Architektonische Vorteile

1. **Weniger Systeme**: 1 System statt 3 (WIRE Gateway + WIRE Dashboard + Hermes)
2. **Keine proprietäre Abhängigkeit**: WIRE ist proprietary, Hermes ist Open Source
3. **Weniger Latenz**: Kein extra Hop zwischen Device und AI-Agent
4. **Eine Konfigurationsoberfläche**: Hermes Dashboard statt zwei Dashboards
5. **Volle Hermes-Features**: Skills, Cron, Delegation, Memory sofort verfügbar

### Was WIRE besser macht (und was wir übernehmen)

1. **Yodel-Protokoll-Compliance** → Übernehmen wir via Adapter
2. **Per-Sentence-TTS-Streaming** → Portierbar nach Hermes
3. **Audio-Cache** → Portierbar nach Hermes
4. **Clean Auth-Separation (Account vs Device)** → Als Pattern übernommen

## Consequences

### Positiv

- Ein System weniger zu maintainen
- Yodel-fähige Devices können sofort mit Hermes sprechen
- Alle Hermes-Features (Skills, Cron, PAI-Delegation) stehen Yodel-Devices zur Verfügung
- Open Source — keine Lizenzprobleme

### Negativ

- Der Yodel-Adapter muss gepflegt werden (ca. 450 Zeilen Python)
- Kein dediziertes Device-Management-Dashboard (Hermes Dashboard deckt es ab, aber weniger Yodel-spezifisch)
- Device-Secret-Rotation ist nicht 1:1 abgebildet (API-Key-Rotation in Hermes existiert)

## Alternatives Considered

### WIRE als Middleware zwischen Device und Hermes

```
Device → Yodel → WIRE Gateway → Hermes
```

**Abgelehnt wegen:**
- 3 Systeme im kritischen Pfad
- WIRE ist proprietary
- WIRE-Dashboard zusätzlich nötig
- Extra Latenz-Hop

### Hermes nativ Yodel sprechen lassen (Core-Integration)

**Abgelehnt wegen:**
- Würde Hermes-Core aufblähen
- Plugin-System ist der richtige Ort für Protokoll-Adapter
- Bessere Trennung von Concerns

## References

- [Yodel Protocol Spec](https://github.com/openyodel/spec)
- [WIRE Gateway Spec](https://github.com/moongrabber/wire-gateway-spec)
- [Hermes Platform Adapter Docs](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)
- [Implementation: `plugins/openyodel-hermes/adapter.py`](../plugins/openyodel-hermes/adapter.py)
