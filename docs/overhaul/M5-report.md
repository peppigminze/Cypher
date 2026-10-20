# M5-Report – LLM-Schicht, Cloud, Persona, Modellwahl

**Status: abgeschlossen.**
- **Ein Agent für alles:** Sprache, Text-Chat und `/api/messages` laufen über dieselbe Engine. Der separate PC-Agent-Prozess ist weg.
- **Cloud:** läuft über die offizielle Responses-API, nur auf ausdrücklichen Wunsch oder nach Rückfrage, mit Kosten-Zählwerk und Monatsbudget.

## 1. Alter Agent entfernt (mit Migration)

| Vorher | Jetzt |
|---|---|
| Text-Chat legte Nachrichten in `/api/messages` ab, ein zweiter Prozess (`agent/run_agent.py`) holte sie ab und verarbeitete sie mit dem alten `CypherAgent` (JSON-Prompts, eigene Freigabe-Tabelle) | **`/api/chat`**: dieselbe Engine wie Sprache, Antwort streamt Wort für Wort (Server-Sent Events), Freigabekarten direkt im Chat, Verlauf bleibt über Neuladen erhalten |
| `/api/sync/*`, `/api/agent/*`, `/api/actions/*`, Heartbeat | entfernt. `/api/status` behält seine Form (`pc_online` = Backend erreichbar, `pending_messages` = offene Freigaben) |
| `/api/messages` POST → „pending“ | **bleibt nutzbar** (z. B. für ein Handy-Kürzel): antwortet jetzt direkt über die Engine. Tabelle und alter Verlauf bleiben unverändert, keine Daten-Migration nötig |

Ebenfalls entfernt wurde toter Code: die alte Bestätigungsleiste, die Offline-Warteschlange und die Browser-Spracherkennung im Chat (Sprache läuft über die Sprachansicht). `start_jarvis.ps1` startet den Agenten nicht mehr. `start_cypher.bat` ersetzt es in M7.

## 2. LLM-Schicht

- `LLMProvider` (`backend/app/llm/base.py`): **eine** Pflichtmethode `stream_chat()`. Sie liefert Textstücke, Tool-Aufrufe und zum Schluss genau ein `StreamDone` mit Nutzung. `chat()` und `health_check()` haben Standardimplementierungen.
- Zwei Implementierungen: `OllamaProvider` (lokal, native API) und `CloudLLMProvider` (Cloud). Die Legacy-Methode `chat_with_tools` und der Alias `LocalLLMProvider` sind entfernt.
- **Validierung, JSON-Retry, Schleifenschutz** liegen in der Engine (seit M3, hier geprüft):
  - Tool-Argumente werden per pydantic validiert (`ArgsModel`).
  - Kaputtes Argument-JSON geht einmal mit Fehlerhinweis zurück ans Modell.
  - Ein identischer Aufruf im selben Turn wird abgelehnt.
  - Pro Turn sind höchstens `AGENT_MAX_STEPS` Modellaufrufe erlaubt.
  - Getestet in `test_engine.py`.

## 3. Cloud – geprüft gegen die offizielle Doku (developers.openai.com, 2026-10-08)

| Prüfpunkt | Ergebnis |
|---|---|
| Modell | `gpt-6-luna` ist OpenAIs Empfehlung für „cost-sensitive, high-volume“ und neuer Standard. `gpt-5.6-luna` (in deiner `.env`) ist **nicht** veraltet, aber teurer. |
| Preise (USD / 1M Tokens: Eingabe / gecacht / Ausgabe) | gpt-6-luna 0.10 / 0.01 / 0.50 · gpt-5.6-luna 0.20 / 0.02 / 1.20 · gpt-6.1-sol 2.00 / 0.10 / 10.00 |
| API | **Responses API** statt Chat Completions: Laut Doku unterstützt Chat Completions *keine Tool-Aufrufe* mit GPT-6 Astra und GPT-6.1 Sol. Mit Responses funktionieren Cyphers Tools mit jedem aktuellen Modell. |
| Parameter | `store=False`: OpenAI speichert nichts. Reasoning-Elemente kommen per `include=["reasoning.encrypted_content"]` mit und gehen mit dem Tool-Ergebnis zurück, wie die Doku es für Reasoning-Modelle verlangt. `reasoning.effort` ist konfigurierbar, Standard „low“ für schnelle Sprachantworten. Keine `temperature`, Reasoning-Modelle nehmen sie nicht. |
| SDK | `openai` 3.26.1 (Apache-2.0). Signaturen und Typen am installierten SDK geprüft, nicht aus dem Gedächtnis. |

**Regeln (nie still in die Cloud):**
1. Die Cloud ist nur nutzbar mit `CLOUD_LLM_ENABLED=true` **und** einem Schlüssel. Sonst gibt es eine klare deutsche Meldung, auch im Einstellungen-Umschalter.
2. **Rückfall nur mit Rückfrage:** Fällt das lokale Modell aus, fragt Cypher: „Mein lokales Modell antwortet gerade nicht. Soll ich es über die Cloud versuchen?“
   - Sichtbar als Karte („Verlässt deinen PC“), zu beantworten per Knopf, J/N oder „ja“/„nein“.
   - Nur nach „ja“ geht **diese eine** Anfrage raus.
   - Die unbeantwortete Frage steht danach nicht doppelt im Verlauf.
3. **Kosten-Zählwerk:**
   - Jede Cloud-Anfrage wird mit den offiziellen Preisen bewertet, inklusive gecachter Tokens.
   - Gespeichert pro Tag und Modell in `token_usage_daily`, mit zwei neuen Spalten per additiver Migration.
   - Sichtbar in den Einstellungen („Cloud-Kosten diesen Monat: 0,0123 $ von 5,00 $ Budget“) und unter `/api/usage`.
4. **Monatsbudget** `CLOUD_MONTHLY_BUDGET_USD` (Standard 5 USD): Ist es erreicht, lehnt die Cloud höflich ab, lokal geht weiter.
5. **Fehler auf Deutsch:** Schlüssel abgelehnt, Limit erreicht, nicht erreichbar, Budget aufgebraucht. Der Schlüssel taucht in keiner Fehlermeldung auf (getestet).

**Getestet ohne echten Schlüssel:** Das echte SDK läuft gegen einen simulierten Responses-Server (httpx MockTransport). Geprüft werden:
- Anfrageform und Streaming-Parsing.
- Die Tool-Runde mit zurückgeschickten Reasoning-Elementen.
- 401, 429 und 400.
- Ein Engine-Durchlauf mit Tool über die Cloud.

**Was fehlt:** ein Live-Aufruf mit echtem Schlüssel. In deiner `.env` ist keiner gesetzt (siehe offene Punkte).

## 4. Persona und Antwortlänge

Gemessen: Jede Stilregel kostet Tool-Treffer, im System-Prompt (M3) wie in der Nutzernachricht (hier). Ein Hinweis „wird vorgelesen, antworte in ein bis zwei Sätzen …“ nur bei Fragen ohne Tool-Bezug brachte beim 9b:

| | Tool-Treffer | ohne Tool behauptet |
|---|---|---|
| ohne Hinweis | 81/84 (96,4 %) | 0 |
| mit Hinweis | 72/84 (85,7 %) | **5** („Ich stelle einen Timer für 90 Sekunden“, ohne Timer) |

Der Hinweis ist deshalb **wieder entfernt**. Die angepasste Länge entsteht so:
- **Kurz von selbst:** Das 9b antwortet ohne Hinweis passend kurz. Antworten ohne Tool haben im Median **52 Zeichen**, „Wie funktioniert ein Kühlschrank?“ bleibt unter 450 Zeichen. Bei „Erklär mir ausführlich …“ wird es lang (Längen-Checks 6/6).
- **Sprechbar gemacht:** Markdown, URLs, Emojis, Zahlen und Uhrzeiten bereinigt `speech_text.py` für die Sprachausgabe, die Untertitel bereinigt `plainText`.
- **Deine Vorlieben:** Antwort-Vorlieben aus dem Gedächtnis („kurze Antworten“) reisen nur mit Fragen ohne Tool mit (M4).

Nebenbei hat die Messung Lücken im Befehls-Wächter gezeigt. „Gib mir in 90 Sekunden Bescheid“, „Ruf die Seite … ab“ und „Mach mir den Editor auf“ erkennt er jetzt auch als Befehle.

## 5. Modellwahl (≥ 25 deutsche Befehle, Standard- und Ersatzmodell)

Befehls-Suite `backend/eval/commands_de.json`, 69 Fälle (alle über die echte Engine, Tools nur simuliert), je 2 Durchläufe:
- 42 Tool-Befehle.
- 10 Wissensfragen mit erwarteter Antwort.
- 3 Längen-Fälle.
- Smalltalk und Ablehnungsfälle.

Zusammengefasst über die beiden Läufe ohne Längen-Hinweis (4 Durchläufe je Modell):

| Kennzahl | qwen3.5:9b | qwen3.5:4b |
|---|---|---|
| Richtiges Tool (42 Befehle × 4) | 161/168 (95,8 %) | **167/168 (99,4 %)** |
| Argumente korrekt | **126/126** | 128/132 (4 von 4 Mal „Dreh auf fünfzig“ → +50 statt 50 %, Ergebnis 80 %) |
| Kein Tool, wo keins nötig | 100/100 | 99/100 |
| Wissensfragen richtig | 53/56 | 53/56 (in einem Lauf „Eiger“ und „1961“) |
| Längen-Checks (kurz bei Erklärungen, lang wenn gewünscht) | **11/12** | 6/12 (nummerierte Listen beim Vorlesen) |
| Behauptet ohne Tool | 0 | 0 |
| Wächter-Eingriffe | 10 | 0 |
| Erster Satz (Median) | 0,73 s | **0,56 s** |
| Grafikspeicher (M0) | 5,8 GB | **3,3 GB** |

Die Antworten des 9b zur Schweiz („Monte Rosa, 4634 m“) sind streng genommen richtig, denn die Dufourspitze ist der Hauptgipfel des Monte-Rosa-Massivs. Die Suite zählt sie trotzdem als falsch.

Ergebnisse pro Fall: `docs/overhaul/bench/commands-qwen3.5-9b.md`, `commands-qwen3.5-4b.md`, `m5-nohint/`, `m5-hint/`.

**Entscheid: `qwen3.5:9b` bleibt Standard, `qwen3.5:4b` bleibt Ersatz.**

Für das 4b spricht:
- Mehr Tool-Treffer und keine Wächter-Eingriffe.
- Schneller (erster Satz ~0,17 s früher).
- Nur 3,3 statt 5,8 GB Grafikspeicher.

Gegen das 4b spricht, was man im Alltag sofort merkt:
- **Ein systematisch falsches Argument**: „Dreh auf fünfzig“ stellt 80 % statt 50 % ein.
- **Zu lange Antworten zum Vorlesen**: Nur die Hälfte der Längen-Checks hält, beim Kühlschrank kommt eine nummerierte Liste.
- **Wissensfehler mit voller Überzeugung** (ein Lauf): „Der höchste Berg der Schweiz ist der Eiger“, „Die Mauer fiel 1961“.

Das 9b hat in frühen M4-Läufen zweimal „17 × 23 = 371“ gerechnet. Beide Modelle können Bilder (`describe_screen`) und Tools.

Umschalten ist eine Zeile in der `.env` (`LOCAL_LLM_MODEL=qwen3.5:4b`). Die Suite misst es neu mit `python -m eval.command_suite --model …`.

## Tests

- Backend: **563 grün**. Neu bzw. neu geschrieben:
  - `test_chat_api.py` (9): Streaming, Fortsetzen, Verlauf, Freigabe im Chat, Cloud-Rückfrage ja/nein/nicht eingerichtet
  - `test_cloud_provider.py` (14): echtes SDK gegen simulierten Server
  - `test_llm_factory.py` (6): Hauptschalter, Schlüssel, Budget
  - `test_cloud_costs.py` (2)
  - Cloud-Rückfrage in der Sprachsitzung
  - `test_messages.py` angepasst
- Frontend: **72 grün**. Neu `ChatView.test.tsx` (4), `chatState.test.ts` (5), Kosten-Zählwerk und Cloud-Status in `SettingsView.test.tsx`.

## Smoke-Test

Echtes Backend (neu gestartet), qwen3.5:9b:

- `POST /api/chat` „Wie viele Kantone hat die Schweiz?“ → Wort-für-Wort-Stream, Antwort „Die Schweiz hat 26 Kantone.“ nach **0,70 s** (erstes Token 0,57 s).
- `POST /api/messages` (alter Weg) „Was ist die Hauptstadt von Italien?“ → sofort `completed`, „Rom.“, `processed_by: local`. Kein Agent-Prozess mehr nötig.
- `GET /api/settings/provider` → `cloud_status: "disabled"`. Umschalten auf Cloud → deutsche Meldung, was in der `.env` fehlt.
- `GET /api/status` → `pc_online: true, llm_available: true`.
- `GET /api/usage` → Tokens heute/Monat, `cost_usd` 0 (nur lokal genutzt).
- Cloud-Rückfrage und Cloud-Antworten sind nur gegen den simulierten Server getestet (kein Schlüssel vorhanden).
- Die Chat-Ansicht ist über Vitest (Streaming, Freigabe, Fehler, Verlauf) und den Live-Endpunkt abgedeckt. Der eingebaute Browser war beim Test ausgeblendet, darum gibt es keinen Klick-Test.

## Offene Punkte

- **Cloud live testen:** Dafür braucht es einen OpenAI-API-Schlüssel in der `.env` (`CLOUD_LLM_ENABLED=true`, `CLOUD_LLM_API_KEY=…`). Ohne Schlüssel ist alles gegen den simulierten Server getestet.
- **Cloud-Modell in deiner `.env`:** Dort steht `CLOUD_LLM_MODEL=gpt-5.6-luna`. Das ist gültig, aber `gpt-6-luna` ist günstiger und empfohlen. Ich habe deine `.env` dafür nicht angefasst.
- **Ein Satz zu viel:** „Ich kann mir nichts mehr merken“ nach einem Vergessen ist ein Modellfehler, den die Suite nicht misst. Er fällt in die Abnahme (M8).
