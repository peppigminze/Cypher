# M0 – Audit: Ist-Zustand von Cypher (Stand 07.10.2026)

Alles in diesem Dokument wurde **real ausgeführt oder gemessen**, nicht aus der
Doku übernommen. Rohdaten der Benchmarks liegen unter `docs/overhaul/bench/`,
die Benchmark-Skripte unter `backend/eval/` (wiederholbar).

## 1. Hardware – verifiziert

| Komponente | Laut Auftrag | Gemessen | Status |
|---|---|---|---|
| CPU | Ryzen 9 9900X, 12C/24T | AMD Ryzen 9 9900X, 12 Kerne / 24 Threads | ✅ |
| RAM | 32 GB DDR5-4800, ~12 GB frei | 2× 16 GB Kingston DDR5-4800, 31,2 GB sichtbar, **12,4 GB frei** | ✅ |
| dGPU | RX 9070 XT, 16 GB | AMD Radeon RX 9070 XT, gfx1201, 16 304 MiB (Treiber 32.0.31041.1004) | ✅ |
| iGPU | – | **Zusätzlich** „AMD Radeon(TM) Graphics" (gfx1036) im Ryzen | ⚠️ siehe unten |
| Desktop-VRAM-Belegung | ~5 GB | 5,4–6,3 GB (Perf-Counter „GPU Adapter Memory – Dedicated Usage" ohne geladenes Modell) | ✅ |

**Ollama nutzt die RX 9070 XT – nachgewiesen** (Ollama 0.40.0, `server.log`):

- Backend: **ROCm 7.1** (`rocm_v7_1\ggml-hip.dll`), Gerät `ROCm0: AMD Radeon RX 9070 XT, gfx1201`.
- `load_tensors: offloaded 29/29 layers to GPU` (qwen2.5:7b), `ollama ps` → `100% GPU`.
- Die iGPU wird korrekt verworfen (`dropping ROCm device — no rocblas support for gfx target gfx1036`
  und `dropping integrated GPU` für Vulkan). Kein Eingriff nötig.
- Flash-Attention ist aktiv (`resolve_fused_ops: Flash Attention enabled`).
- Generierung qwen2.5:7b: ~102 tok/s, Prompt-Verarbeitung ~400 tok/s kalt / >10 000 tok/s mit Prompt-Cache.

**Aber: „100 % GPU" in `ollama ps` ist unter Windows kein Beweis für gute Performance**
(siehe Befund K1). Mit drei gleichzeitig geladenen Modellen zeigte `ollama ps` weiterhin
überall „100% GPU", tatsächlich waren **8,9 GB in den geteilten System-RAM ausgelagert**
(Perf-Counter „Shared Usage") und die Antwortzeiten 10–20× langsamer.

## 2. Abweichungen zwischen Doku/Auftrag und Realität

| Behauptung | Realität |
|---|---|
| Projektordner `JARVIS_silvan` | Existiert nicht. Projekt liegt in `Cypher/` (Remote `peppigminze/Cypher`). |
| `cypher-projektplan.md` lesen | **Existiert nicht** (Suche über `C:\Users\Silvan`, Tiefe 6). Wird in M8 neu angelegt. |
| Tests: 257 Backend / 61 Frontend grün | **262 Backend / 61 Frontend grün** (real ausgeführt). |
| Cloud via „OpenAI SDK" | Rohes `httpx` gegen `/chat/completions`, kein SDK. |
| Tools: …, `search_files`, … | Im Tool-Chat ist es `find_files` (eigener Sucher über `SEARCH_DIRECTORIES`); `search_files` existiert separat für `ALLOWED_DIRECTORIES`. |
| README: Voice über Web Speech API | Veraltet: Voice-View nutzt eigenes Whisper/Piper; ChatView nutzt noch Web Speech API (Google/Microsoft-Server!). |
| README: venv `backend\venv`, Modell `llama3.1:8b`, Cloud `gpt-4o-mini` | Real: `backend\.venv`, `qwen2.5:7b`, `.env.example` sagt `gpt-5.6-luna`. |
| Uncommittete Arbeit | 43 geänderte/neue Dateien lagen uncommittet vor → als Baseline-Commit `69dfbe8` auf `overhaul/v1` gesichert. |
| Laufende Instanz | Backend + Agent laufen bereits über die geplante Aufgabe **„JARVIS Autostart"** (`scripts/start_jarvis.ps1`, startet uvicorn direkt → umgeht die Token-Prüfung in `run.py`). |

Bestätigt wie beschrieben: FastAPI + SQLite/SQLAlchemy, `NativeToolCallingAgent` + `ToolExecutor`,
Approval-Gate für `write_file` (funktioniert real), faster-whisper (base/cpu/int8) mit PyAV,
sherpa-onnx + `de_DE-thorsten-medium` (Modell vorhanden), Persona in `persona.py`,
Provider-Toggle + Token-Zähler in SQLite, Bind-Adresse/CORS/Token-Hardfail, PTT-Bubble mit
State-Machine idle/listening/thinking/confirming/speaking.

## 3. Smoke-Tests gegen die laufende Instanz (real)

| Aufruf | Ergebnis |
|---|---|
| `GET /health` | `{"status":"ok"}` – statisch, prüft nichts. `/api/health` → 404. |
| `POST /api/tool-chat` ohne Token | 401 ✅ |
| „Wie spät ist es?" | **„Es ist 19:38 Uhr UTC."** – lokale Zeit war 21:38 ❌ |
| „Pausiere die Musik." | 1. Versuch `media_control(action="pause")` → Fehler, 2. Versuch `play_pause` ✅ (Toggle – würde laufende *und* pausierte Musik umschalten) |
| „Schreib eine Notiz in notiz.txt …" | Approval-Karte (`pending_confirmation`) ✅ |
| Approval **abgelehnt** | Modell antwortet **„Erledigt."** ❌ (irreführend) |
| `POST /speak` | WAV ok, 4,4 s Audio |
| `POST /transcribe` | „Hallo **Sylvan**, … die Musik ist **posiert**" (base), 0,85 s warm / 3,4 s kalt |

Hinweis: Der Smoke-Test „Pausiere die Musik" hat auf dem PC einmal die Play/Pause-Medientaste ausgelöst.

## 4. Latenz pro Pipeline-Stufe (Vorher-Messung)

Gemessen über die echten Endpunkte (`/transcribe` → `/api/tool-chat` → `/speak`), synthetische
Sprachaufnahme, qwen2.5:7b, 9 Durchläufe:

| Stufe | Median warm | Bemerkung |
|---|---|---|
| Aufnahme-Ende erkennen | **manuell** (Push-to-Talk) | kein VAD |
| STT (whisper base) | 0,90 s | kalt 1,6–3,4 s (Modell-Lazy-Load) |
| LLM (Tool-Chat, nicht gestreamt) | 1,19 s | 2 LLM-Calls bei Tool-Nutzung; Antwort erst komplett |
| TTS (ganze Antwort) | 0,27 s | Piper selbst nur ~0,05 s/Satz warm (RTF 0,03) |
| **Gesamt bis Audio** | **2,52 s** | **11,9 s**, wenn Ollama das Modell entladen hatte (Keep-Alive 5 min) |

Größter Block warm: STT + zwei nicht-gestreamte LLM-Calls. Größter Block kalt: Modell-Neuladen.

## 5. Modell-Benchmark (LLM)

`python -m eval.bench_llm …` – Ollama native `/api/chat`, `num_ctx=8192`, `think=false`,
`temperature=0`, 44 deutsche Testfälle (32 Basis + 12 schwere: Umgangssprache, Distraktoren,
Injection, Zahlenextraktion), Ziel-Tool-Katalog mit 17 Tools. Je **ein** Modell geladen.

| Modell | VRAM (Modell) | VRAM System gesamt | Gen tok/s | Tool-Treffer | Ø Tool-Latenz | Kaltstart |
|---|---|---|---|---|---|---|
| qwen2.5:7b (bisher) | 5,1 GB | 10,5 GB | 100 | 91 % (40/44) | 0,25 s | 6,6 s |
| **qwen3.5:9b** | 5,8 GB | 12,2 GB | 78 | **98 % (43/44)** | 0,72 s* | 6,6 s |
| **qwen3.5:4b** | 3,3 GB | 9,6 GB | 106 | **98 % (43/44)** | 0,49 s* | 4,9 s |
| gemma4:12b | ~9 GB** | 14,8 GB | 78 | 98 % (43/44) | 0,69 s | 9,2 s |
| qwen3:14b (≈14B) | 10,5 GB | **16,0 GB** (= Kartenlimit) | 54 | 95 % (42/44) | 0,43 s | 7,9 s |
| llama3.1:8b | 5,9 GB | 11,2 GB | 99 | 77 % (34/44, 6× unnötiges Tool) | 0,23 s | 7,7 s |

\* Ohne Prompt-Cache-Treffer gemessen (Qwen 3.5 ist ein Hybrid-Modell). Im realistischen
Mehr-Turn-Test mit ~2 400 Prompt-Tokens greift der Cache: **TTFT 0,13 s**, Tool-Entscheidung
0,46 s (4B) / 0,60 s (9B); qwen2.5:7b 0,05 s / 0,32 s.
\** `ollama ps` meldet für gemma4 unplausible 1,3 GB; Delta im System-Counter ≈ 9 GB.

**Antwortqualität (frei, Deutsch)** – nicht nur Speed:

- qwen2.5:7b: „17 mal 23" → **„211"** (falsch, richtig 391), siezt („Hier ist ein Witz für Sie"),
  sinnloser Witz. Verfehlt Dateisuche-Befehle (3×).
- qwen3.5:4b/9b, gemma4:12b, qwen3:14b: alle Rechen-/Wissensfragen korrekt, duzen.
  qwen3.5:4b nutzt Emojis (Persona muss das verbieten).
- **Alle** Modelle halluzinieren das Datum, wenn es nicht im Kontext steht („21. Mai 2023").
- `think` muss explizit `false` sein: Qwen 3.5 denkt sonst per Default (bis 271 statt 26 Tokens
  für einen Tool-Call, 5–35 s).

**Entscheidung:**

- **Standard: `qwen3.5:9b`** – beste Gesamtqualität im Budget, 5,8 GB VRAM inkl. 8k-Kontext →
  ~4,5 GB Reserve im 10-GB-Budget. Lizenz Apache-2.0.
- **Schneller Fallback: `qwen3.5:4b`** – gleiche Tool-Trefferquote, 3,3 GB, 106 tok/s.
  Schwäche: Zahlenextraktion („Dreh mal auf fünfzig" → `direction=up`).
- **Verworfen:** `qwen3:14b` (das ≈14B-Modell) – brachte das System auf 16,0 GB dedizierten VRAM,
  also bei normaler Desktop-Last sicher Auslagerung. `gemma4:12b` – ~9 GB, gleiche Trefferquote
  wie 9B, langsamer Kaltstart. `qwen2.5:7b` – bleibt per Config nutzbar, aber nicht mehr Default.

## 6. Whisper-Benchmark (CPU)

`python -m eval.bench_whisper` – 24 Clips (12 deutsche Befehle, sauber + verrauscht ~15 dB SNR),
**synthetisch** mit der Piper-Stimme erzeugt (echte Mikrofonaufnahmen: `--wav-dir`, siehe Skript).
WER zählt „10" vs. „zehn" als Fehler – absolute Werte daher pessimistisch, Vergleich ist fair.

| Modell | Threads | Beam | initial_prompt | Latenz Median | WER |
|---|---|---|---|---|---|
| base | 8 | 5 | nein | 0,41 s | 34,4 % |
| base | 8 | 1 | nein | 0,37 s | 37,6 % |
| small | 8 | 5 | ja | 1,15 s | 18,8 % |
| **small** | 12 | 5 | **ja** | 1,15 s | **12,6 %** |
| small | 8 | 1 | nein | 1,10 s | 21,2 % |
| large-v3-turbo | 12 | 5 | ja | 3,81 s | 10,1 % |

- `small` halbiert die Fehlerquote gegenüber `base` für +0,7 s. `initial_prompt` mit „Cypher"
  macht aus „Hi, Süfer" ein korrektes „Hi Cypher".
- Mehr als 8 Threads bringt keine Latenz (Encoder verarbeitet immer ein 30-s-Fenster).
- `large-v3-turbo` ist genauer, aber 3,8 s sind für Sprachsteuerung zu langsam.
- **Entscheidung:** `small`, int8, `cpu_threads=8`, `beam_size=5`, `language="de"` fix,
  `initial_prompt` mit Eigennamen. GPU bleibt exklusiv fürs LLM.
- Heute fehlt `language` → Whisper erkennt die Sprache jedes Mal selbst (Fehlerquelle bei
  kurzen Äußerungen, Zusatzzeit).

## 7. Befunde (priorisiert)

### Kritisch (K)

- **K1 – VRAM-Überbelegung unter Windows wird verschwiegen.** Ollama lädt standardmäßig mehrere
  Modelle parallel (`OLLAMA_MAX_LOADED_MODELS` auto). Unter Windows/WDDM lagert der Treiber den
  Überlauf in den geteilten RAM aus; `ollama ps` zeigt trotzdem „100% GPU". Gemessen: 14,8 GB
  dediziert + 8,9 GB geteilt, Tool-Call 5,4 s statt 0,3 s.
  *Fix:* Ein-Modell-Politik (`OLLAMA_MAX_LOADED_MODELS=1` + Cypher entlädt Fremdmodelle),
  Health-Check auf „Shared Usage" der GPU, `doctor`-Prüfung.
- **K2 – Kaltstart nach 5 min Leerlauf.** Ollama-Keep-Alive-Default 5 min → erste Anfrage nach
  einer Pause 11,9 s statt 2,5 s. *Fix:* `keep_alive` konfigurierbar mitsenden, Warm-up beim Start.
- **K3 – Falsche Uhrzeit.** `get_current_time` liefert UTC, das Modell sagt „19:38 Uhr UTC".
  *Fix:* lokale Zeitzone (`zoneinfo`), Datum/Uhrzeit zusätzlich direkt in den Kontext (spart einen
  LLM-Roundtrip für die häufigste Frage).
- **K4 – Kein „leiser"/„lauter".** Es gibt kein Lautstärke-Tool (Abnahmetest Punkt 3 nicht erfüllbar);
  `media_control` kennt nur das Toggle `play_pause`.
- **K5 – Token im Frontend-Bundle.** `VITE_USER_TOKEN` wird ins JS gebacken und vom Service-Worker
  vorgecacht; wer die PWA laden kann, hat den Token.

### Hoch (H)

- **H1 – Ablehnung wird als „Erledigt." gemeldet.** Nach Nein erzeugt das Modell eine
  Erfolgsmeldung. *Fix:* deterministische Antwort bei Ablehnung + Persona-Regel.
- **H2 – Kontextfenster unkontrolliert.** Ollama-Default `num_ctx=4096` (aus VRAM abgeleitet);
  der OpenAI-kompatible Endpunkt erlaubt kein `num_ctx`. Mit Memory + Verlauf läuft der Kontext
  still über (llama.cpp `--context-shift` schneidet dann den Anfang weg – inkl. Systemprompt-Teilen).
  *Fix:* nativer `/api/chat` mit `options.num_ctx`, harte Token-Budgets.
- **H3 – Prompt-Injection.** Tool-Ergebnisse (Dateien, Webseiten) gehen ungekennzeichnet als
  JSON in den Verlauf. Keine Tests dafür.
- **H4 – Kein Audit-Log.** Nur `run_command` wird protokolliert; Genehmigungen/Ablehnungen und
  alle anderen Tool-Ausführungen nicht. Approvals liegen nur im RAM (Neustart = verloren).
- **H5 – `fetch_url`-SSRF-Lücke.** `follow_redirects=True`: Ein Redirect auf eine interne Adresse
  wird **ausgeführt**, erst danach wird die Ziel-URL geprüft (nur die Antwort wird verworfen).
  DNS-Rebinding (TOCTOU zwischen Prüfung und Request) ebenfalls offen.
- **H6 – `web_search` ist ein Platzhalter**, der `success=True` mit leerer Liste liefert →
  das Modell erfindet Antworten oder behauptet, nichts gefunden zu haben.
- **H7 – Keine Streaming-Pipeline.** Antwort wird erst komplett generiert, dann komplett
  synthetisiert, dann komplett heruntergeladen.
- **H8 – Kein VAD, kein Wake-Word, kein Barge-in**, Bubble während thinking/speaking nicht
  unterbrechbar; `getUserMedia` wird für jede Äußerung neu geöffnet.
- **H9 – Health ist Attrappe.** `/health` liefert immer „ok"; kein Zustand von Ollama/Modell/GPU/
  Whisper/TTS/DB. Kein `/api/diagnostics`.
- **H10 – Kein Rate-Limiting** auf Token-geschützten Endpunkten (Token-Brute-Force bei
  Tailscale-Bindung nur durch Tokenlänge begrenzt).

### Mittel (M)

- **M1 – Zwei parallele Agent-Pipelines.** `CypherAgent` (JSON-Envelope, `core.py`, 389 Zeilen) läuft
  in einem **separaten Agent-Prozess**, der alle 5 s pollt (ChatView, Tasks); `NativeToolCallingAgent`
  bedient die Voice-View. Doppelte Logik, doppelter Approval-Mechanismus (`PendingAction` vs.
  `pending_approvals`), +0–5 s Latenz im Text-Chat, zweiter Prozess im Betrieb.
- **M2 – Logging** nur `basicConfig` auf die Konsole; Dateien entstehen nur durch Umleitung im
  Startskript, ohne Rotation, ohne Request-/Pipeline-IDs, ohne Latenzmetriken.
- **M3 – Config** ohne Validierung (z. B. `WHISPER_MODEL_SIZE=smal` fällt erst beim ersten Request
  auf), Magic Numbers in Modulen (`MAX_STEPS`, `PENDING_TTL_SECONDS`, Timeouts, Limits).
- **M4 – Blockierende Calls im Event-Loop:** `LocalLLMProvider` öffnet pro Request einen neuen
  `httpx.AsyncClient` (kein Connection-Reuse); `MemoryStore`/SQLAlchemy-Sync-Calls direkt in
  `async`-Tools; `list_running_applications`/`ram_usage` synchron.
- **M5 – Pfad-Sandbox:** keine Systemordner-Sperre (wer `C:\` freigibt, gibt `C:\Windows` frei),
  keine Größenprüfung *vor* dem Lesen (`read_bytes()` liest die ganze Datei, dann wird gekürzt),
  keine Schreib-Größenlimits.
- **M6 – Memory** ist eine LIKE-Suche ohne Kernspeicher im Prompt; `save_memory` ist SAFE und
  kann durch Tool-Ergebnisse (Injection) ausgelöst werden.
- **M7 – Kein Gesprächsverlauf** im Voice-Pfad: jede Anfrage startet ohne Kontext
  („und morgen?" funktioniert nicht).
- **M8 – Autostart** startet uvicorn direkt (umgeht `run.py`-Token-Prüfung), wartet nicht auf
  Health, startet Ollama nicht, ein Agent-Prozess zusätzlich.

### Niedrig (N)

- **N1** – Kein ESLint/Prettier, kein Playwright-E2E. `npm audit`: 6 Schwachstellen in Dev-Tools
  (vite 5/esbuild).
- **N2** – Doku-Drift (siehe Abschnitt 2), README beschreibt teils V1-Zustand.
- **N3** – `starlette.testclient`-Deprecation-Warnung (httpx), harmlos.
- **N4** – Abhängigkeiten nicht gepinnt (`>=`), keine `docs/DEPENDENCIES.md`.

## 8. Tote/abgekoppelte Code-Pfade

- `frontend/public/stt-test.html`, `tts-test.html` – Testseiten aus der Voice-Entwicklung.
- `useVoiceInput.ts`/`useSpeechOutput.ts` + `VoiceOrb.tsx` – Web-Speech-API (Cloud-STT des
  Browsers!) im ChatView; durch eigene Pipeline ersetzbar.
- `get_llm_provider()`/`get_cloud_llm_provider()` + automatischer Cloud-Fallback in `core.py` –
  widerspricht der neuen Regel „nie stillschweigend in die Cloud".
- `agent/` (Client, Sync-Worker), `/api/sync/*`, `/api/actions/*`, `/api/agent/heartbeat` –
  nur für den alten Pfad.
- `GetSystemStatusTool`, `CpuUsageTool`, `RamUsageTool` – durch `get_system_info` abgedeckt.

## 9. Entscheidung zum alten `CypherAgent`-Pfad

**Entfernen (in M5), nicht nur deprecaten.** Begründung:

1. Er löst kein Problem, das der native Pfad nicht löst – alle aktuellen Modelle können natives
   Tool-Calling (Benchmark: 98 % mit Qwen 3.5).
2. Er hält einen zweiten Prozess, Polling-Latenz, einen zweiten Approval-Mechanismus und einen
   stillen Cloud-Fallback am Leben – Komplexität ohne Nutzen und ein Sicherheitsrisiko.
3. Das „PC ist aus"-Versprechen der Offline-Queue ist laut eigener `ARCHITECTURE.md` nicht
   erreichbar (Backend läuft auf demselben PC).

Migration ohne Datenverlust: Tabellen (`messages`, `tasks`, `memory_entries`, `pending_actions`, …)
bleiben erhalten; Text-Chat läuft künftig über denselben Agenten wie Voice (Streaming), Tasks/
Reminder bleiben über Tools + Scheduler nutzbar; Memory wird in M4 per Migrationsskript übernommen.

## 10. Single Points of Failure

| SPOF | Auswirkung heute | Maßnahme |
|---|---|---|
| Ollama-Prozess | 503 mit englischem Text, UI zeigt Fehlertext roh | Health + deutsche Meldung + Auto-Recovery (M1) |
| VRAM-Kontingent | stilles 10–20× Slowdown | Ein-Modell-Politik + Shared-Usage-Check (M1/M7) |
| SQLite-Datei | ein File, kein Backup | Backup/Export-Befehl (M7) |
| In-Memory-Approvals | Neustart verliert offene Freigaben | Persistieren + Audit-Log (M2) |
| Token in `.env`/Bundle | Leck = Vollzugriff | generierter Token, nicht im Bundle, Pairing (M2) |

## 11. Abhängigkeiten / Lizenzen (Ist)

Alle Laufzeit-Abhängigkeiten sind MIT/BSD/Apache-2.0. Transitiv: `certifi`, `tqdm` (MPL-2.0,
schwaches Datei-Copyleft, kein GPL). Kein GPL-Paket. Details: `docs/DEPENDENCIES.md`.
Modelle: Qwen 3.5 (Apache-2.0, laut `ollama show`), Whisper (MIT), Silero VAD v6 (MIT, liegt in
`faster-whisper` bei). Piper-Stimme thorsten: Datensatz CC0 (MODEL_CARD), aber feinabgestimmt
von der englischen „lessac"-Stimme, deren Trainingsdaten (Blizzard 2013) nur nicht-kommerziell
lizenziert sind – für privaten Gebrauch unkritisch, für Weitergabe/kommerziell zu prüfen.
**openWakeWord:** Code Apache-2.0, aber die vortrainierten Modelle sind **CC BY-NC-SA 4.0**
(nicht permissiv) und es gibt kein „Hey Cypher" ohne eigenes Training → nicht verwendbar
(siehe PLAN, M3).
