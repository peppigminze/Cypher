# Überholungsplan Cypher v1 (Ergebnis aus M0)

Basis: [AUDIT.md](AUDIT.md). Jeder Meilenstein endet mit grünen Tests, realem Smoke-Test,
Report `docs/overhaul/MX-report.md` und Commit auf `overhaul/v1`.

## Getroffene Grundsatzentscheidungen

| Thema | Entscheidung | Begründung (Kurz) |
|---|---|---|
| Standard-LLM | `qwen3.5:9b`, `think=false`, `num_ctx=8192` | 98 % Tool-Treffer, korrekte Antworten, 5,8 GB VRAM |
| Fallback-LLM | `qwen3.5:4b` | 98 %, 3,3 GB, 106 tok/s |
| Ollama-Anbindung | nativer `/api/chat` statt OpenAI-Kompat-Endpunkt | nur dort: `num_ctx`, `keep_alive`, `think`, Streaming mit Tool-Calls, Timing-Felder |
| Modellpolitik | genau **ein** Chat-Modell im VRAM, Keep-Alive konfigurierbar (Default 30 min), Warm-up beim Start | K1/K2 |
| STT | faster-whisper `small`, int8, CPU, 8 Threads, Beam 5, `language=de`, `initial_prompt` | WER 12–19 % bei 1,15 s; GPU bleibt fürs LLM |
| VAD | Silero VAD v6 (ONNX, liegt `faster-whisper` bei → keine neue Abhängigkeit) | MIT, CPU, < 1 ms pro 32-ms-Frame |
| Wake-Word | eigener `WhisperKeywordSpotter`: VAD-Segment ≤ 3 s → Whisper `base` mit Prompt „Hey Cypher" → unscharfer Abgleich | openWakeWord: Modelle CC BY-NC-SA + kein „Hey Cypher" ohne Training (Training ist out of scope) |
| Audio-Transport | eine WebSocket-Verbindung pro Client (16-kHz-PCM hoch, Events + PCM-Audio runter) | Streaming in beide Richtungen, Barge-in ohne Request-Ping-Pong |
| Text-Chat-Transport | Server-Sent Events | einfach, proxy-freundlich, reicht für unidirektionales Token-Streaming |
| Alter `CypherAgent` + Agent-Prozess | **entfernen** in M5 (Daten bleiben) | AUDIT §9 |
| Tool-System | Klassen mit `name`, pydantic-`Args`, `risk`, `execute()`, Auto-Registrierung per Decorator | personalJarvis-Muster, eigene Umsetzung |
| Risk-Tiers | `read_only` (auto) / `reversible` (Bestätigung) / `destructive` (Bestätigung + Warnung) / `blocked` | Auftrag M2 |
| Web-Suche | DuckDuckGo-HTML-Endpunkt per `httpx` (kein API-Key, keine neue Abhängigkeit), SearXNG optional per Config | lokal-first, keine Pflicht-Accounts |
| Wetter | Open-Meteo (API-frei, kein Key) | kostenlos, CC BY 4.0 Daten |
| Lautstärke | Windows Core Audio per `pycaw` (MIT) | einzig robuster Weg für Master-Volume |
| Frontend-State | explizite, getestete Zustandsmaschine (Entscheidung XState v5 vs. eigene Transitionstabelle in M6) | Auftrag M6 |
| Auslieferung | Backend liefert gebautes Frontend aus (ein Port, ein Prozess) | Auftrag M7 |

## Angepasste Meilenstein-Reihenfolge (mit Begründung)

Die Reihenfolge M1 → M8 bleibt, aber drei Arbeitspakete werden **vorgezogen**, weil spätere
Meilensteine sonst nicht testbar wären:

1. **LLM-Provider auf nativen Ollama-Endpunkt + Modell-Manager → in M1** (statt M5).
   Ohne ihn kann `/api/health` weder „Modell geladen" noch „GPU genutzt" ehrlich melden, und
   Keep-Alive/Warm-up (K2) sind über den OpenAI-Kompat-Endpunkt nicht steuerbar.
   Das `LLMProvider`-Protocol inkl. Streaming wird dabei gleich im Zielformat angelegt.
2. **Einheitliche Streaming-Konversations-Engine → in M3** (statt M5). Die Voice-Pipeline
   braucht tokenweises Streaming mit Tool-Calls und Cancellation. M5 baut darauf auf
   (Cloud-Provider, Retry, Testsuite, Persona) statt einen zweiten Agenten zu schreiben.
3. **Minimaler Audio-Client im Frontend (AudioWorklet + WebSocket) → in M3** (statt M6).
   VAD/Wake-Word/Barge-in lassen sich ohne echten Client nicht ende-zu-ende prüfen.
   M6 macht danach das Redesign und die formale State-Machine.

## Maßnahmen je Meilenstein

### M1 – Fundament & Stabilität
- `app/core/config.py`: pydantic-settings mit Typen, Grenzen, `Literal`s, Pfadprüfung;
  verständliche deutsche Fehlermeldung beim Start; alte Variablennamen bleiben gültig
  (keine Breaking Changes). `.env.example` neu strukturiert.
- `app/core/logging.py`: Konsole + rotierende Datei (`logs/cypher.log`), JSON-fähiges Format,
  Request-ID-/Pipeline-ID per `contextvars`, Middleware setzt `X-Request-ID`.
- `app/core/metrics.py`: Latenz pro Stufe (stt/llm_ttft/llm_total/tool/tts/e2e), Ringpuffer,
  Perzentile für Diagnose-UI.
- `app/core/errors.py`: Fehlerkatalog → deutsche Meldung + Handlungsanweisung (UI + TTS-tauglich),
  einheitlicher Exception-Handler.
- `app/llm/protocol.py` + `app/llm/ollama.py` (nativ, Streaming, geteilter `httpx.AsyncClient`,
  Timeouts) + `app/llm/model_manager.py` (Warm-up, Keep-Alive, Fremdmodelle entladen,
  Status: erreichbar/Modell vorhanden/geladen/VRAM/Shared-Auslagerung).
- `/api/health` (Ampel je Komponente) + `/api/diagnostics` (Details, Latenzen, GPU, Versionen);
  `/health` bleibt als Liveness-Alias.
- Graceful Startup (Warm-up im Hintergrund, Server ist sofort erreichbar) und Shutdown
  (Tasks canceln, Clients schließen).
- Kein Absturz bei Ollama offline / Modell fehlt / Timeout → deutsche Meldung.
- `get_current_time` mit lokaler Zeitzone (K3).
- Tests: Config-Validierung, Health-Zustände (Ollama down/up via Fake-Server), Fehlerkatalog,
  Provider-Streaming-Parser, ToolExecutor, Approval-Gate, Provider-Switch.

### M2 – Sicherheit & Tool-System
- Token beim ersten Start generieren (`data/secrets.json` bzw. `.env`), nicht mehr ins Bundle;
  localhost-UI ohne Token, Remote-Geräte per Pairing (Token einmal eingeben / QR).
- Rate-Limiting (In-Memory-Token-Bucket) für Auth-Fehler, Chat, Voice, Approvals.
- Plugin-Registry (`app/tools/registry.py` neu), pydantic-Argument-Validierung, Risk-Tiers,
  persistente Approvals + Audit-Log-Tabelle (`tool_audit`) inkl. Genehmigt/Abgelehnt/Abgelaufen.
- Pfad-Sandbox: erlaubte Wurzeln, Systemordner-Sperre, Symlink/Junction-Auflösung,
  Größenlimits vor dem Lesen, Schreiblimits.
- Untrusted-Markierung aller Tool-Ergebnisse + ≥ 3 Injection-Tests.
- `web_search` (DuckDuckGo-HTML, optional SearXNG), `fetch_url` ohne automatische Redirects
  (jeder Hop geprüft, IP-Pinning gegen DNS-Rebinding).
- Neue Tools: `set_volume`/`get_volume`, `media_control` (play/pause/next/previous getrennt),
  `open_app` (Whitelist), `set_timer`/Erinnerung (Scheduler), Zwischenablage lesen/schreiben,
  `save_note` (Notizordner + Memory-Anbindung in M4), `get_weather` (Open-Meteo).
  Screenshot-Beschreibung: nur wenn Vision-Modell im Budget (qwen3.5:9b hat Vision, ~0,5 GB
  Projektor schon geladen) – Entscheidung nach VRAM-Messung in M2.

### M3 – Voice-Pipeline
- `app/voice/`: Protocols `STTEngine`, `TTSEngine`, `VAD`, `WakeWordDetector`; Implementierungen
  faster-whisper, sherpa-onnx Piper, Silero ONNX, WhisperKeywordSpotter.
- `ConversationEngine` (Streaming-Tool-Loop, Cancellation per `asyncio`-Task, Approval-Pause).
- Satz-Segmentierer + TTS-Textnormalisierung (Uhrzeiten, Zahlen, Einheiten, Markdown raus).
- WebSocket `/api/voice/ws`: Zustände idle → wake-listening → listening → thinking → speaking →
  (confirming); Barge-in per Wake-Word oder `interrupt`-Nachricht; Echo-Schutz (Browser-AEC +
  serverseitiger Abgleich mit gerade gesprochenem Text + Mic-Gating-Option).
- Frontend: AudioWorklet-Capture (16 kHz PCM16), PCM-Wiedergabe-Queue, Reconnect.
- Ziel-Latenz (vom Sprechende bis erstes hörbares Wort, warm): **≤ 2,0 s ohne Tool,
  ≤ 2,8 s mit einem Tool** (inkl. 0,6 s VAD-Nachlauf). Vorher/Nachher-Messung im Report.
- whisper.cpp/Vulkan: bewusst nicht (GPU-Konkurrenz mit dem LLM, nativer Build, Gewinn ~1 s
  wird durch VRAM-Risiko K1 erkauft) – Begründung im Report.
- Streaming-STT: bewusst nicht (Whisper-Encoder arbeitet in 30-s-Fenstern; Teiltranskripte
  würden die CPU-Last vervielfachen; Nutzen < Aufwand).

### M4 – Memory
- `data/memory/core_memory.json` (Kernfakten, immer im Prompt, hartes Token-Budget),
  SQLite-FTS5-Archiv (Gesprächsturns + Fakten), Relevanzauswahl per FTS5-BM25,
  Kurzzeitverlauf mit Token-Budget + Zusammenfassung, optionaler Curator-Job → `memory/`-Wiki.
- Tools `remember`/`forget`/`recall` (Schreiben nur auf expliziten Nutzerwunsch; Tool-Ergebnisse
  dürfen kein Memory schreiben → Poisoning-Test), Migration der bestehenden `memory_entries`.
- API + UI-Editor (ansehen, bearbeiten, löschen).

### M5 – LLM-Schicht & Persona
- `OpenAIProvider` über das offizielle OpenAI-SDK (Parameter gegen aktuelle Doku geprüft),
  identisches Tool-Calling über das gemeinsame Protocol; Modellnamen nur aus Config.
- Fallback-Vorschlag bei lokalem Fehler/Timeout (nie automatisch, Kosten sichtbar).
- Robustheit: pydantic-Validierung, Retry bei kaputtem JSON, Loop-Schutz, klare Abbruchmeldung.
- Testsuite ≥ 25 Befehle (vorhanden: 44) gegen die **echte Registry** für Standard + Fallback.
- Persona neu (TTS-tauglich, adaptive Länge Voice/Text), zentral editierbar.
- Alten `CypherAgent`-Pfad + Agent-Prozess entfernen.

### M6 – Frontend
- Design-Tokens, Bubble (Canvas, audio-reaktiv, reduced-motion), Untertitel, Wake/VAD-Status,
  Approval-Karte (Klick/Tastatur/Sprache), verstecktes Menü (Einstellungen, Audit-Log,
  Memory-Editor, Diagnose, Logs), designte Fehlerzustände, Mobile/Safe-Areas.
- Formale State-Machine mit Interrupt-Pfaden + Tests; WS-Reconnect; AudioWorklet;
  ESLint/Prettier; Vitest + Playwright-Smoke; PWA ohne API-Caching, Update-Hinweis.

### M7 – Betrieb
- `setup.bat`, `start_cypher.bat`/`.ps1`, `stop_cypher.bat`, `doctor` (inkl. GPU-/Shared-VRAM-
  Prüfung), Autostart neu, Wake-on-LAN prüfen, Backup/Export, Frontend vom Backend ausliefern,
  `docs/MOBILE.md` (Tailscale + HTTPS via `tailscale serve`).

### M8 – Abschluss
- README, `docs/ARCHITECTURE.md`, `cypher-projektplan.md` (neu, da nicht vorhanden),
  Abnahmetest, `docs/ACCEPTANCE.md`, `docs/overhaul/FINAL.md`.

## Bekannte externe Grenzen (keine Blocker)

- **Kein Cloud-API-Key** in `.env` → Cloud-Provider wird gegen einen lokalen OpenAI-kompatiblen
  Fake-Server und mit Mocks getestet; Live-Test mit echtem Key steht als manuelle Checkliste in
  `docs/ACCEPTANCE.md`.
- **Mikrofon/Handy** sind in dieser Umgebung nicht automatisch bedienbar → VAD/Wake-Word/Barge-in
  werden mit eingespielten WAV-Streams über den echten WebSocket getestet, plus manuelle Checkliste.
- `OLLAMA_MAX_LOADED_MODELS=1` ist eine Ollama-Server-Umgebungsvariable (Benutzer-Env +
  Ollama-Neustart). Cypher erzwingt die Ein-Modell-Politik zusätzlich selbst; `setup`/`doctor`
  setzen/prüfen die Variable.
