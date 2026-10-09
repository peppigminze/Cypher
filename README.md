# Cypher

Ein Sprachassistent im Stil von JARVIS für deinen Windows-PC. Du sagst „Hey Cypher, wie spät ist es?“ und bekommst nach rund 2 Sekunden eine gesprochene Antwort. Alles läuft **lokal**: Das Sprachmodell rechnet in Ollama auf deiner Grafikkarte, Spracherkennung (Whisper) und Stimme (Piper) laufen auf der CPU. Die Cloud ist optional und wird nie automatisch benutzt.

```mermaid
flowchart LR
    Du(["Du<br/>(Mikrofon / Tastatur)"]) -- "Sprache über WebSocket<br/>Text über SSE" --> UI["Oberfläche<br/>(Browser / Handy-App)"]
    UI --> Backend["Cypher-Backend<br/>127.0.0.1:8000"]
    Backend --> STT["Whisper<br/>(hört zu)"]
    Backend --> Engine["Gesprächs-Engine"]
    Engine --> LLM["Ollama<br/>qwen3.5:9b auf der GPU"]
    Engine --> Tools["Tools<br/>Lautstärke, Musik, Notizen,<br/>Timer, Wetter, Suche …"]
    Engine --> Mem["Gedächtnis<br/>(Kern + Archiv)"]
    Engine -. "nur nach deinem „Ja“" .-> Cloud["Cloud-Modell<br/>(optional)"]
    Backend --> TTS["Piper<br/>(spricht)"]
```

Wie das im Detail zusammenspielt: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Was Cypher kann

| Bereich | Beispiele |
|---|---|
| Sprechen | „Hey Cypher, wie spät ist es?“, „Hey Cypher“ … Pause … „Wie wird das Wetter morgen in Bern?“. Unterbrechen per Tippen, Leertaste oder „Hey Cypher, stopp“. |
| PC steuern | „Mach die Musik leiser“, „Pause“, „Nächster Titel“, „Stell einen Timer auf 10 Minuten“, „Öffne den Rechner“, „Kopier das in die Zwischenablage“ |
| Wissen & Nachschauen | Allgemeinwissen direkt; Wetter, Websuche, Webseiten zusammenfassen, Dateien finden, „Was läuft gerade?“, „Was siehst du auf meinem Bildschirm?“ |
| Notizen | „Schreib eine Notiz: Milch kaufen“ (mit Freigabekarte), „Zeig meine Notizen“ |
| Gedächtnis | „Merk dir, dass ich freitags Berufsschule habe“, „Vergiss das mit dem Kaffee“. Alles sichtbar und editierbar im Menü → Gedächtnis. |
| Handy | Über Tailscale mit HTTPS, Kopplung per QR-Code ([docs/MOBILE.md](docs/MOBILE.md)) |

Riskante Aktionen (Notiz schreiben, Datei ändern, App schliessen …) brauchen deine Freigabe per Klick, Taste **J/N** oder „ja“/„nein“. Alles, was Cypher tut, steht im Audit-Log.

## Schnellstart

**Voraussetzungen** (einmal installieren):
- Windows 10/11, eine Grafikkarte mit mindestens 8 GB Speicher (getestet: AMD RX 9070 XT, 16 GB)
- [Python 3.12](https://www.python.org/downloads/) (beim Installieren „Add python.exe to PATH“ anhaken)
- [Node.js LTS](https://nodejs.org/)
- [Ollama](https://ollama.com/download)

**Dann im Projektordner per Doppelklick:**

| Datei | Wofür |
|---|---|
| `setup.bat` | Einmal (und nach Updates): richtet alles ein, lädt Stimme und – nach Rückfrage – die Sprachmodelle (mehrere GB), prüft am Ende alles |
| `start_cypher.bat` | Startet Cypher unsichtbar im Hintergrund und öffnet **http://127.0.0.1:8000** |
| `stop_cypher.bat` | Beendet Cypher sauber |
| `doctor.bat` | Prüft die Installation und sagt auf Deutsch, was fehlt (ändert nichts) |
| `backup_cypher.bat` | Sichert Gedächtnis, Notizen und Verlauf nach `backups\`. Einspielen: `backup_cypher.bat --restore backups\<datei>.zip` |

Autostart bei der Anmeldung (optional, fragt einmal nach Administrator-Rechten):

```bash
backend\.venv\Scripts\python.exe scripts\autostart.py install
```

## Bedienung

- **Sprachansicht:** der leuchtende Kern in der Mitte. Oben stehen Modus, Mikrofon und die letzte Antwortzeit.
- **Modi:** *Hey Cypher* (hört dauerhaft auf das Aktivierungswort), *Tippen* (tippen, sprechen, Cypher merkt selbst, wann du fertig bist), *Funk* (tippen zum Starten, nochmal tippen zum Senden).
- **Tasten:** Leertaste = Mikrofon / unterbrechen, **Esc** = abbrechen, **J/N** = Freigabe ja/nein, **M** = Menü.
- **Menü (M):** Chat (schreiben statt sprechen), Gedächtnis, Aufgaben, Projekte, Handy, Diagnose, Audit-Log, Logs, Einstellungen (lokal/Cloud, Kosten).
- **Wenn etwas fehlt** (Ollama aus, Stimme fehlt …), steht oben ein Banner: was kaputt ist, was das heisst und was zu tun ist. Läuft es wieder, verschwindet das Banner von selbst. Ein Neustart ist nicht nötig.

## Sicherheit in Kürze

- Das Backend hört nur auf `127.0.0.1`. Das Handy kommt über `tailscale serve` (HTTPS, nur deine Geräte).
- Jede Anfrage braucht einen Zugangscode. Am PC holt die Oberfläche ihn selbst, andere Geräte koppeln sich einmal.
- Jedes Tool hat eine **Risikostufe**: lesen = sofort, steuern = sofort, schreiben/löschen = nur mit deiner Freigabe, manche Tools sind gar nicht freigeschaltet.
- **Prompt-Injection:** Was aus Dateien, Webseiten, Zwischenablage oder Bildschirm kommt, gilt als unsicher. Danach sind Gedächtnis und Zwischenablage-Schreiben für diesen Turn gesperrt, auch per Freigabe nicht zu öffnen. Alles andere, das etwas verändert, zeigt dir ohnehin erst eine Freigabekarte mit genau dem, was passieren würde.
- **Cloud** nur, wenn du sie einschaltest (Einstellungen) oder bei einem Ausfall die angebotene Rückfrage mit „Ja“ beantwortest. Monatsbudget und Kostenzähler sind eingebaut.
- Kein Schlüssel im Repo: `.env` ist gitignored, `.env.example` beschreibt alle Einstellungen.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#sicherheit).

## Konfiguration

Alle Einstellungen stehen kommentiert in `.env.example`; `setup.bat` kopiert sie nach `.env`. Die wichtigsten:

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `LOCAL_LLM_MODEL` / `LOCAL_LLM_FALLBACK_MODEL` | `qwen3.5:9b` / `qwen3.5:4b` | Sprachmodell in Ollama (Wahl begründet in `docs/overhaul/M5-report.md`) |
| `WHISPER_MODEL_SIZE`, `WAKE_WORD_MODEL` | `small`, `tiny` | Spracherkennung / Aktivierungswort |
| `WAKE_WORD_PHRASE` | `Hey Cypher` | Aktivierungswort |
| `VAD_SILENCE_MS` | `700` | So lange Stille, bis Cypher annimmt, dass du fertig bist |
| `MEMORY_CORE_MAX_TOKENS` | `400` | Grösse des Kern-Gedächtnisses |
| `CLOUD_LLM_ENABLED`, `CLOUD_LLM_API_KEY`, `CLOUD_LLM_MODEL` | aus, –, `gpt-6-luna` | Optionale Cloud (OpenAI Responses API) |
| `CLOUD_MONTHLY_BUDGET_USD` | `5` | Harte Kostengrenze pro Monat |
| `ALLOWED_DIRECTORIES` | leer | Ordner, in denen Cypher Dateien lesen/schreiben darf (leer = aus). Die Suche nach Dateinamen nutzt `SEARCH_DIRECTORIES` (leer = dein Benutzerordner). |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Adresse des Backends |

## Wake-on-LAN

Ein schlafender PC kann sich nicht selbst wecken, und ein Browser kann kein Magic Packet senden. Deshalb gibt es `scripts/wake_pc.py`: ein Skript ohne Abhängigkeiten, das du auf einem **anderen** Gerät im selben Heimnetz startest (`python wake_pc.py AA:BB:CC:DD:EE:FF`). Voraussetzungen am PC: Wake-on-LAN im BIOS, im Geräte-Manager „Wake on Magic Packet“ beim Netzwerkadapter, am besten per Kabel. `doctor.bat` prüft die Netzwerkkarte, sobald `WAKE_ON_LAN_MAC` gesetzt ist.

## Fehlerbehebung

Zuerst immer `doctor.bat`. Häufige Fälle:

| Symptom | Lösung |
|---|---|
| Banner „Ollama läuft nicht“ | Ollama starten (Startmenü) oder `start_cypher.bat`. Cypher verbindet sich von selbst neu. |
| Antworten sehr langsam (> 10 s) | `doctor.bat` → Zeile *Grafikkarte*: Liegt das Modell nicht komplett im Grafikspeicher, andere Modelle in Ollama beenden. Dauerhaft hilft die Benutzer-Umgebungsvariable `OLLAMA_MAX_LOADED_MODELS=1`. |
| Keine Stimme | `setup.bat` erneut (lädt die Piper-Stimme) |
| Mikrofon geht auf dem Handy nicht | Nur über die `https://…ts.net`-Adresse, siehe [docs/MOBILE.md](docs/MOBILE.md) |
| `start_cypher.bat` meldet „startet nicht“ | `logs\backend.err.log` ansehen, oder `start_cypher.bat -Foreground` zeigt die Fehler direkt |
| Port 8000 belegt | `stop_cypher.bat` (beendet auch ältere Cypher-Instanzen) |

## Entwicklung

```bash
cd backend && .venv\Scripts\python.exe run.py --reload
```

```bash
cd frontend && npm run dev
```

Der Vite-Dev-Server läuft auf http://localhost:5173 und spricht das Backend über `VITE_API_BASE_URL` aus `frontend/.env` an.

Tests:

| Was | Befehl (aus `backend/` bzw. `frontend/`) |
|---|---|
| Backend (pytest, ~590 Tests, ~1 min) | `.venv\Scripts\python.exe -m pytest -q` |
| Frontend (Vitest) | `npm test` |
| Lint, Typen, Format | `npm run lint`, `npx tsc -b`, `npm run format:check` |
| Browser-Smoke-Test (Playwright, im installierten Edge) | `npm run test:e2e` |
| **Abnahmetest** gegen eine isolierte echte Instanz | `.venv\Scripts\python.exe -m eval.acceptance` ([docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)) |
| Befehls-Suite gegen das echte Modell (ohne Wirkung auf den PC) | `.venv\Scripts\python.exe -m eval.command_suite` |
| Sprach-Szenarien über den echten WebSocket | `.venv\Scripts\python.exe -m eval.voice_client all` |

## Dokumentation

| Datei | Inhalt |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Aufbau, Datenflüsse, Sicherheit, Entscheidungen mit Messwerten |
| [docs/MOBILE.md](docs/MOBILE.md) | Handy über Tailscale (HTTPS, Kopplung, App-Verknüpfung) |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | Abnahme: Definition of Done, Ergebnis, manuelle Checkliste |
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | Alle Abhängigkeiten mit Lizenz (nur MIT/Apache/BSD) |
| [cypher-projektplan.md](cypher-projektplan.md) | Ziel, Stand, nächste Schritte, bewusst nicht im Umfang |
| [docs/overhaul/](docs/overhaul/) | Audit, Plan und Berichte M0–M8 der Überholung |
