# M3-Report – Voice-Pipeline

**Status: abgeschlossen.** Gesprochen wird jetzt über eine dauerhafte WebSocket-Verbindung:
- Der Server erkennt selbst, wann du fertig gesprochen hast.
- Die Antwort wird Satz für Satz vorgelesen, während das Modell noch schreibt.
- Tippen oder „Hey Cypher“ unterbricht sofort.

## Was gebaut wurde

| Teil | Datei(en) | Was es tut |
|---|---|---|
| Gesprächs-Engine | `backend/app/agent/engine.py` | **Ein** Agent für Sprache, Text-Chat und `/api/tool-chat`. Streamt Sätze, führt Tools nur über den `ToolExecutor` aus, pausiert für Freigaben, lässt sich abbrechen. Schutz gegen Endlosschleifen: Schritt-Limit, Wiederholungs-Sperre und Rückfrage bei kaputtem JSON. |
| Sprachsitzung | `backend/app/voice/session.py`, `backend/app/api/voice_ws.py` | Protokoll und Zustände (`idle`, `wake_listening`, `listening`, `thinking`, `speaking`, `confirming`). Modi: Aktivierungswort, Tippen, Funk (Push-to-Talk). Dazu Barge-in, Echo-Schutz, Freigabe per „ja“/„nein“ und angesagte Timer. |
| Pausenerkennung | `backend/app/voice/vad.py`, `segmenter.py` | Silero-VAD v6 (aus faster-whisper, MIT). Hysterese, 300 ms Vorlauf. Nach 250 ms Pause startet die Spracherkennung schon vorsorglich (Ergebnis wird verworfen, wenn du weitersprichst). |
| Aktivierungswort | `backend/app/voice/wakeword.py` | „Hey Cypher“ ohne trainiertes Modell, siehe unten |
| Satz-Zerlegung, Sprechtext | `backend/app/voice/sentences.py`, `speech_text.py` | Deutsche Satzgrenzen (z. B., 3.5, „1. Oktober“). Uhrzeiten, Grad, Prozent und Daten werden sprechbar, Markdown, Emojis und URLs fallen weg. |
| Kurzbefehle | `backend/app/agent/quick_commands.py` | „Pause“, „Weiter“, „Leiser“, „Lauter“, „Nächster Titel“, „Ton aus“ laufen **ohne LLM** (trotzdem über Executor und Audit-Log) |
| Wächter „behauptet, aber nicht getan“ | `backend/app/agent/action_guard.py` | Eine kurze Erfolgsmeldung ohne Tool-Aufruf wird zurückgehalten und nie gesprochen. Danach folgt ein Versuch mit Hinweis, sonst eine ehrliche Antwort. |
| Browser: Mikrofon | `frontend/src/audio/captureWorklet.ts`, `downsampler.ts` | AudioWorklet: 48 kHz → 16 kHz PCM16 im Audio-Thread, alle 40 ms ein Paket. Die Echo-Unterdrückung des Browsers ist an. |
| Browser: Wiedergabe | `frontend/src/audio/pcmPlayer.ts` | Sätze lückenlos hintereinander geplant. `stop()` schweigt sofort. |
| Browser: Verbindung | `frontend/src/services/voiceClient.ts` | Token in der ersten Nachricht, nie in der URL. Automatisch neu verbinden (0,5–15 s). Bei ungültigem Token neu koppeln. Herzschlag alle 15 s. |
| Browser: Zustand + Ansicht | `frontend/src/hooks/voiceReducer.ts`, `useVoiceSession.ts`, `components/CypherVoiceView.tsx`, `CypherBubble.tsx` | Zustandsmaschine als reiner Reducer (testbar). Die Bubble reagiert auf die Lautstärke, Live-Untertitel, Freigabekarte (Klick, Tasten J/N, Stimme), Modus-Umschalter, Texteingabe. Ohne Animation bei `prefers-reduced-motion`. |

Die alte Kette (WebM aufnehmen → `/transcribe` → `/api/tool-chat` → `/speak`) ist aus der Sprachansicht entfernt (`useCypherVoiceState.ts` gelöscht). Die HTTP-Endpunkte bleiben für den Text-Chat und die Kompatibilität erhalten.

## Latenz vorher / nachher

Gemessen mit `python -m eval.voice_client latency`. Synthetische Sprache wird in Echtzeit (20-ms-Frames) über den echten WebSocket gestreamt, das Backend läuft normal (qwen3.5:9b auf der GPU, Whisper small auf der CPU).

| Messpunkt | Vorher (M0, alte Kette) | Nachher (M3) |
|---|---|---|
| Ende der Aufnahme bis erstes Audio (vergleichbarer Wert) | **2,52 s**, Ende musste per Tippen signalisiert werden | **0,58–1,05 s** ab erkanntem Sprechende |
| Ende des Sprechens bis erste Silbe, alles inklusive | – (kein VAD) | **1,24 s** einfache Frage (Folge-Turn), **1,46 s** erster Turn einer Sitzung, **1,61 s** mit Tool (Wetter). Darin stecken 0,7 s bewusste Wartezeit (`VAD_SILENCE_MS`), ob du fertig bist. |
| Spracherkennung im kritischen Pfad | 0,90 s (whisper base) | **0 s** in 3/3 Läufen: Die vorsorgliche Erkennung war beim Sprechende schon fertig (Whisper small, genauer als base) |
| Kalt (Modell entladen) | 11,9 s | Warm-up beim Start, Keep-Alive 30 min, automatische Wiederherstellung (M1) |
| Unterbrechen per Tippen | nicht möglich | Ton weg nach **2 ms** (Server), sofort im Browser |
| Unterbrechen per „Hey Cypher“ | nicht möglich | **0,68 s** nach deinem Sprechende |
| „Hey Cypher, wie spät ist es?“ in einem Atemzug | – | 1,78 s bis zur ersten Silbe |
| „Hey Cypher“ … Pause … Frage | – | 1,10 s bis zur ersten Silbe |
| Kurzbefehl „Pause“ | voller LLM-Turn | kein LLM, nur Tool-Zeit |
| Freigabe per Stimme („Ja.“) bis Bestätigung | – | 0,2 s bis „Notiz gespeichert.“ |

Bei langen Tool-Antworten kommt die Zwischenmeldung („Ich schaue nach.“) als Erstes. Langsame Tools (Wetter, Suche, Webseite, Bildschirm) melden sich so einmal pro Turn.

**Woher die verbleibende Zeit kommt (erster Turn):** 0,7 s Pausenerkennung, ~0,3 s Prefill von ~1000 nicht gecachten Tokens, ~0,1 s erstes Token, ~0,15 s TTS des ersten Satzes. Das Modell ist ein Hybrid (Gated DeltaNet). Ollama kann den Cache deshalb nur an Checkpoints fortsetzen: Bei einem neuen Gespräch kommen nur 1275 von ~2300 Prompt-Tokens aus dem Cache, ab dem zweiten Turn praktisch alle (2292/2361). Gemessen mit den neuen Turn-Metriken `llm_N_uncached_tokens` und `llm_N_prefill`.

## Der Notiz-Bug und was er gezeigt hat

Bei „Schreib eine Notiz …“, „Stell einen Timer …“ und „Mach leiser“ hat qwen3.5:9b oft nur „Notiz gespeichert.“ oder „Leiser gestellt.“ geantwortet, ohne ein Tool aufzurufen. Im frischen Test traf das 9 von 10 Befehle.

Ursache, eingegrenzt mit 10 Befehlen × mehrere Prompt-Varianten:

| System-Prompt | Tool-Treffer |
|---|---|
| alter Prompt (Persona + Tool-Regeln + Abschnitt „Sprachausgabe: kurz, ohne Markdown …“) | 2/10 |
| nur der Abschnitt „Antworte knapp, ohne Markdown, Emojis oder URLs.“ dazu | 3/10 |
| Persona + Tool-Regeln, **ohne** Stil-Abschnitt | 18/20 |

Jede Stilregel für die Sprachausgabe im System-Prompt lässt das Modell Tools überspringen. Auch Umformulieren, Umsortieren und Hinweise in der Nutzernachricht halfen nicht. Qwen 3.5 setzt den System-Text direkt hinter den Tool-Block, eine Stilregel dort wirkt wie „einfach antworten“.

Lösung:
- Prompt ohne Stil-Abschnitt (`backend/app/persona.py`, Begründung im Docstring).
- Sprechbarkeit macht `speech_text.py`.
- Kurzbefehle ohne LLM.
- Der Wächter (s. o.) als Sicherheitsnetz.

Bonus-Fund: Ein Argument namens `title` verschwand aus allen Tool-Schemas (Schema-Bereinigung). Ist repariert.

### Deutsche Befehls-Suite (Grundlage für M5)

`python -m eval.command_suite`, 50 Fälle in `backend/eval/commands_de.json`. Sie laufen über die echte Engine, Tools nur simuliert (Dry-Run, nichts wird am PC ausgelöst). Zwei Durchläufe pro Modell:

| Kennzahl | qwen3.5:9b (Standard) | qwen3.5:4b (Ersatz) |
|---|---|---|
| Richtiges Tool bei Befehlen (39 Fälle × 2) | **77/78 (98,7 %)** | **78/78 (100 %)** |
| Kein Tool, wo keins nötig ist (11 × 2) | 22/22 | 22/22 |
| Argumente korrekt | 60/60 | 58/60 („Dreh auf fünfzig“ → +50 statt 50 %) |
| Inhaltlich richtig (17 × 23, Hauptstadt, Name …) | 8/8 | 8/8 |
| Behauptet ohne Tool | **0** | **0** |
| Davon vom Wächter abgefangen | 1 | 0 |
| Erster Satz (Median, nur LLM-Fälle) | 0,77 s | 0,54 s |
| Kurzbefehle (ohne LLM) | 16 | 16 |

Vor dem Prompt-Fix lag derselbe Kern (10 Befehle) bei **2/10**.

Einziger verbliebener Fehlgriff beim 9b: „Brich den Timer ab.“ ohne laufenden Timer. Das Modell fragt zurück, statt `cancel_timer` aufzurufen.

Die Gedächtnis-Fälle („Merk dir …“ / „Vergiss …“) sind für M4 vorbereitet und werden dort mitgemessen. Dass das 4b-Modell hier so gut abschneidet, nehme ich in M5 in den Modellvergleich auf (Standard bleibt vorerst 9b wegen Argument-Genauigkeit und Allgemeinwissen).

Details pro Fall: `docs/overhaul/bench/commands-qwen3.5-9b.md` / `commands-qwen3.5-4b.md`.

## Entscheidungen

- **Whisper bleibt auf der CPU (faster-whisper small, int8, 8 Threads).**
  - whisper.cpp mit Vulkan wäre auf der RX 9070 XT möglich (MIT). Es würde aber VRAM und Rechenzeit mit dem LLM teilen (Single-Model-Policy aus M0) und eine native Binary mitbringen.
  - Weil die vorsorgliche Erkennung bei der 250-ms-Pause startet, lag Whisper in allen Messungen gar nicht mehr im kritischen Pfad. Der Gewinn wäre ≈ 0.
- **Kein Wort-für-Wort-Streaming der Spracherkennung.**
  - Whisper ist kein Streaming-Modell. Zwischenergebnisse hiessen, ein wachsendes Fenster immer wieder neu zu transkribieren (CPU-Last, schlechtere Erkennung).
  - Die vorsorgliche Erkennung bringt den Latenzgewinn ohne diese Kosten. Deinen Satz zeigt die UI deshalb erst nach dem Sprechende.
- **Aktivierungswort ohne trainiertes Modell.**
  - openWakeWord: Code Apache-2.0, aber alle vortrainierten Modelle CC BY-NC-SA, kein „Hey Cypher“, Training ausserhalb des Scopes.
  - Stattdessen: Pausenerkennung → Whisper tiny auf den ersten 2,5 s → phonetischer Abgleich („Cypher“, „Saifer“, „Seifer“; „Karl Cypher.“ als falsch gehörtes „Hey Cypher.“).
  - Ehrliche Grenzen: reagiert erst nach der Äusserung (oder ihren ersten 2,5 s) und kostet ~0,3 s CPU pro gehörter Äusserung. Jede Äusserung im Raum wird lokal transkribiert, nichts verlässt den PC.
  - Hinter einem Protokoll austauschbar, sobald ein trainiertes Modell verfügbar ist. Tippen und Funk funktionieren immer.
- **Echo-Schutz zweistufig.**
  - Stufe 1: die Echo-Unterdrückung des Browsers.
  - Stufe 2 auf dem Server (`ECHO_GUARD=aec_text`): Ein „Hey Cypher“, dessen Rest nur wiederholt, was Cypher gerade gesagt hat, wird ignoriert.
  - Kurze Antworten wie „ja“ zählen nie als Echo.
  - Alternative für Lautsprecher ohne AEC: `ECHO_GUARD=mic_gate`. Dann gibt es während Cypher spricht kein Zuhören, also kein Barge-in per Stimme.
- **Verlauf bei Unterbrechung ehrlich.**
  - Gespeichert wird nur, was du tatsächlich gehört hast (gesendete Audio-Sätze), mit Marker „[vom Nutzer unterbrochen]“.
  - Gefunden durch Tests: Vorher landete die Teilantwort erst beim Aufräumen durch den Garbage Collector im Verlauf, jetzt sofort (`contextlib.aclosing`).
- **Datenschutz im Browser:** Audio verlässt den Browser im Tipp-Modus nur, solange der Server zuhört, im Funk-Modus nur solange gedrückt. Nur im Aktivierungswort-Modus wird dauerhaft gestreamt, an den eigenen PC (`shouldStreamAudio`, getestet).

## Tests

- Backend: **530 grün** (`pytest`), neu:
  - `test_engine.py` (20): Streaming, Tools, Freigaben, Barge-in-Verlauf, Wächter, Kurzbefehle, Verlaufsfenster
  - `test_voice_session.py` (12): getippter und gesprochener Turn, Funk, Halluzinationsfilter, Tipp-Unterbrechung mit ehrlichem Verlauf, neue Eingabe unterbricht, Freigabe per „Ja“ / unklar / „Nein“ / Klick, Aktivierungswort in einem Atemzug, kein Fehl-Wecken, eigene Stimme ist kein Barge-in
  - `test_voice_units.py` (42): Sätze, Sprechtext, ja/nein, Aktivierungswort, Endpunkt-Erkennung
  - `test_quick_commands_guard.py` (36)
- Frontend: **83 grün** (`vitest`), neu:
  - Downsampler (48 und 44,1 kHz)
  - AudioWorklet in simulierter Worklet-Umgebung, **auch minifiziert wie im Produktions-Build**. Dieser Test hat einen echten Fehler gefunden: Klassenfelder wurden zu Hilfsfunktionen kompiliert, die es im Worklet nicht gibt.
  - Player (lückenlos, Fertig-Meldung, Stopp)
  - WebSocket-Client (Auth zuerst, Header + Binär-Paarung, Backoff, 4401 ohne Wiederholung)
  - Reducer (ganzer Turn, Freigabe überlebt „spricht“, Nachzügler nach Unterbrechung ignoriert, Datenschutz-Regel)

## Smoke-Test (real ausgeführt)

1. **Im eingebauten Browser** (Vite-Dev + Backend), Mikrofon ersetzt durch Piper-Sprache über einen MediaStream. So lief der echte AudioWorklet-Pfad, ohne den Raum mitzuschneiden.
   - Getippt: „Wie spät ist es?“ → Antwort gestreamt und abgespielt, erste Silbe nach 1,7 s, danach zurück auf „bereit“.
   - Gesprochen: „Wie wird das Wetter morgen in Bern?“ → exakt erkannt, Wetter-Tool, Antwort gesprochen.
   - Gesprochen: „Notiere: Brot und Milch kaufen.“ → Freigabekarte „Notiz „Einkaufsliste“ speichern …“. Gesprochenes „Ja.“ → `data/notes/2026-10-08.md` enthält den Eintrag, im Audit-Log stehen `approval_requested` und `approved` (Quelle `voice`).
   - Lange Erklärung, Antippen während Cypher spricht → Ton stoppt, Cypher hört wieder zu.
   - Dabei gefunden und behoben: Die Freigabekarte verschwand, solange Cypher die Frage vorlas. Nachzügler-Text der abgebrochenen Antwort lief noch in die Untertitel. Die Untertitel zeigten rohes Markdown.
2. **`python -m eval.voice_client all`** (echter WebSocket, Echtzeit-Audio): Latenz, Aktivierungswort (auch mit Pause dazwischen, nach Fix), kein Fehl-Wecken bei normalem Gespräch, Barge-in per Tippen und Stimme, Echo-Test, Freigabe per Stimme. Alles grün.

## Manuelle Checkliste (echtes Mikrofon, braucht dich)

Am PC, `http://localhost:5173` (bzw. später `start_cypher.bat`):

- [ ] Bubble antippen → Browser fragt nach dem Mikrofon → erlauben → „Ich höre zu …“
- [ ] „Wie spät ist es?“ sagen, nicht nochmal tippen → Cypher merkt das Satzende selbst und antwortet (unten steht „Antwort nach … s“)
- [ ] Während Cypher spricht, antippen → sofort still
- [ ] Modus „Hey Cypher“ → „Hey Cypher, mach die Musik leiser“ (Spotify läuft) → leiser, ohne Rückfrage
- [ ] „Hey Cypher“ … kurze Pause … „Pause“ → Musik pausiert
- [ ] Cypher über die **Lautsprecher** (nicht Kopfhörer) etwas Langes erzählen lassen und **nichts** sagen → Cypher unterbricht sich nicht selbst
- [ ] Während Cypher spricht „Hey Cypher, stopp“ → bricht ab
- [ ] „Notiere: Testnotiz“ → Karte erscheint, „Ja“ sagen → gespeichert; nochmal mit „Nein“ → nichts gespeichert
- [ ] Normales Gespräch im Raum (ohne „Cypher“) → keine Reaktion
- [ ] Handy über Tailscale: kommt in M7 (HTTPS nötig, sonst sperrt der Browser das Mikrofon. Die UI sagt das jetzt auch so)

Wenn das Aktivierungswort bei dir schlecht klappt: `WAKE_WORD_THRESHOLD` (Standard 0,85) etwas senken oder `WAKE_WORD_MODEL=base` probieren. Den Debug-Log dazu (`cypher.voice.wake`) schickst du mir.

## Offene Punkte (gehen in spätere Meilensteine)

- **Länge gesprochener Antworten (M5):** Ohne Stilregel antwortet das Modell auf „Erzähl ausführlich …“ lang und mit Markdown (wird für die Sprache bereinigt, Untertitel auch). Die adaptive Länge baue ich in M5 so, dass die Tool-Treffer nicht leiden. Jede Prompt-Änderung muss durch die Befehls-Suite.
- **Gespräch über Neuverbindungen hinweg (M4):** Jede WebSocket-Sitzung startet heute ein neues Gespräch (Verlauf nur im Speicher).
- **Design (M6):** Die Sprachansicht ist funktional. Die JARVIS-Optik, XState, Playwright und das Menü folgen in M6.
- **Alter Agent-Prozess (M5):** `JARVIS Autostart` startet ihn noch mit. Er sendet Heartbeats an `/api/agent/*` und wird mit dem alten CypherAgent abgelöst.
