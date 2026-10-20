# M1-Report – Fundament & Stabilität

## Was geändert wurde

| Bereich | Änderung | Dateien |
|---|---|---|
| Config | Validiert mit Typen/Grenzen/erlaubten Werten; deutsche Fehlermeldung mit Pfad zur `.env`; Secrets werden in Fehlern nie ausgegeben; alle alten Variablennamen gültig; neue Defaults aus dem M0-Benchmark (`qwen3.5:9b`, Whisper `small`, `num_ctx=8192`, `keep_alive=30m`, `think=false`) | `backend/app/config.py`, `.env.example` |
| Logging | Konsole (lesbar) + rotierende JSON-Datei `logs/cypher.log`; Request-ID/Pipeline-ID per `contextvars`; Uvicorn-Logs integriert; laute Bibliotheken gedämpft | `app/core/log.py`, `app/core/context.py`, `app/core/middleware.py` |
| Metriken | Latenz je Stufe (http, llm_ttft, llm_total, stt, tts, …) mit p50/p90 | `app/core/metrics.py` |
| Fehler | Fehlerkatalog → kurze deutsche Meldung (sprechbar) + Handlungsanweisung; einheitliches JSON `{"detail", "error": {code, message, hint, retryable, request_id}}` | `app/core/errors.py` |
| LLM | Nativer Ollama-Provider (`/api/chat`, Streaming inkl. Tool-Calls, `num_ctx`, `keep_alive`, `think`, gepoolter HTTP-Client, Timeouts); Fehlerklassen (Modell fehlt / Timeout / kaputte Antwort / nicht erreichbar); `LocalLLMProvider` bleibt als Alias kompatibel | `app/llm/base.py`, `app/llm/ollama.py`, `app/llm/http.py`, `app/llm/local_provider.py`, `app/llm/factory.py` |
| Modell-Lebenszyklus | Warm-up mit Produktionsoptionen, Ein-Modell-Politik (entlädt andere Modelle), Status inkl. GPU-Anteil, Monitor erkennt Ollama-Neustart und lädt neu (mit Backoff) | `app/llm/model_manager.py` |
| GPU | Windows-Perf-Counter per `ctypes` (keine neue Abhängigkeit): dedizierter/geteilter VRAM des Inferenz-Prozesses → erkennt die stille Auslagerung (AUDIT K1); echte VRAM-Größe aus der Registry | `app/core/gpu.py` |
| Health | `/api/health` (öffentlich, Ampel je Komponente: database, ollama, llm_model, gpu, stt, tts, deutsche Texte), `/api/diagnostics` und `/api/logs` (Token); `/health` unverändert | `app/core/health.py`, `app/api/health.py` |
| Start/Stop | Non-blocking Warm-up von LLM, Whisper und Stimme im Hintergrund; sauberes Herunterfahren (Tasks canceln, HTTP-Clients schließen); `run.py` zeigt Config-Fehler lesbar an | `app/runtime.py`, `app/main.py`, `backend/run.py` |
| Bugfix K3 | `get_current_time` liefert lokale Zeit + deutsches Datum (`TIMEZONE` optional, `tzdata` ergänzt) | `app/tools/system_tools.py` |
| STT | Feste Sprache, Beam, `initial_prompt`, Threads aus Config; VAD-Filter gegen Halluzinationen bei Stille; Warm-up | `app/stt/whisper_provider.py` |
| TTS | Warm-up (erster `generate()` ist ~20× langsamer), Status für Health | `app/tts/piper_provider.py` |
| API-Fehler | `/api/tool-chat`, `/transcribe`, `/speak` nutzen den Fehlerkatalog statt roher Exception-Texte | `app/api/*.py` |
| Tests | Test-Umgebung isoliert (eigene Log/Data-Ordner, Ollama auf geschlossenem Port, keine Modell-Warm-ups) | `tests/conftest.py` |

`.env` (deine): `LOCAL_LLM_MODEL` → `qwen3.5:9b`, `WHISPER_MODEL_SIZE` → `small`,
`LOCAL_LLM_BASE_URL` → `http://127.0.0.1:11434`. Sicherung: `.env.pre-overhaul.local` (gitignored).

## Was getestet wurde

**Automatisch:** 331 Backend-Tests grün (262 bestehende + 69 neue), 61 Frontend-Tests grün.
Neue Tests decken ab: Config-Validierung (8 Fehlerfälle, Secret-Schutz, Rückwärtskompatibilität),
Ollama-Provider gegen gescriptetes Fake-Ollama (Streaming, Tool-Calls, kaputte Argumente,
404/Timeout/Verbindungsfehler/abgebrochener Stream), Modell-Manager (Warm-up-Optionen,
Ein-Modell-Politik, Erholung nach Ollama-Neustart, Auslagerungs-Erkennung), Health-API
(Ollama down/ok/Modell fehlt/Auslagerung), Request-IDs, Fehlerformat, ToolExecutor,
Approval-Gate (pausiert, genau einmal ausgeführt, Ablehnung, BLOCKED, Ablauf, Schritt-Limit),
STT/TTS-Parameter, deutsche Datumsformatierung.

**Real (gegen laufenden Server + echtes Ollama):**

| Smoke-Test | Ergebnis |
|---|---|
| Kaltstart → `/api/health` alles grün | nach ~12 s (`qwen3.5:9b` 100 % GPU, 8192 Kontext, Warm-up 10,9 s; Whisper small 1,9–2,1 s) |
| GPU-Erkennung | „AMD Radeon RX 9070 XT … 11,2 / 15,9 GB belegt", Inferenz-Prozess 0,13 GB geteilt (gesund) |
| „Wie spät ist es?" | **„Es ist 22:34 Uhr am Mittwoch, 7. Oktober 2026."** (vorher: „19:38 Uhr UTC"), 1,5 s |
| Ollama beendet → `/api/health` | `error`, „Ich erreiche mein Sprachmodell gerade nicht." + Hinweis „Starte Ollama …" |
| Ollama beendet → `/api/tool-chat` | HTTP 503 mit deutschem Fehlerobjekt, Backend läuft weiter |
| Ollama neu gestartet | Monitor erkennt es, lädt Modell selbst; Health nach ~19 s wieder `ok` – **ohne Backend-Neustart** |
| Logdatei | JSON-Zeilen mit Request-ID, Rotation konfiguriert |

Hinweis: Ein Smoke-Test („Pausiere die Musik") hat erneut die Play/Pause-Medientaste gesendet.

## Offen / bewusst verschoben

- Tool-Chat ist noch nicht gestreamt und hat keinen Verlauf → M3 (Konversations-Engine).
- Antwort nach Ablehnung („Erledigt.") → M2 (Approval-Gate-Härtung).
- Ollama-Server-Variable `OLLAMA_MAX_LOADED_MODELS=1` wird erst in M7 (`setup`/`doctor`) gesetzt;
  bis dahin erzwingt Cypher die Ein-Modell-Politik selbst beim Start.
- Beim Ollama-Neustart kann ein Health-Aufruf bis zu 2 s dauern (Probe-Timeout, vorher 5 s).
- Der alte Agent-Prozess schreibt ~4 MB Polling-Log pro Tag ohne Rotation → entfällt mit M5.
- Die Autostart-Aufgabe startet noch die alte Variante (uvicorn direkt + Agent) → M7.
