# M7-Report – Betrieb

**Status: abgeschlossen.** Cypher lässt sich jetzt ohne Kommandozeile einrichten, starten, stoppen, prüfen und sichern. Die Oberfläche kommt vom Backend selbst: **http://127.0.0.1:8000** ist die einzige Adresse. Fürs Handy gibt es eine Anleitung mit HTTPS über Tailscale und eine Kopplung per QR-Code.

## Die fünf Dateien im Projektordner

| Datei | Was sie tut |
|---|---|
| `setup.bat` | Einmalig bzw. nach Updates: Python-Umgebung + Pakete, `.env` aus `.env.example`, Oberfläche bauen, Piper-Stimme laden, fehlende Ollama-Modelle **nach Rückfrage** laden, am Ende der Doktor. Ein gescheiterter Schritt bricht nicht ab; am Schluss steht „Einrichtung unvollständig“ mit der Liste, was fehlt (Exit-Code 1). |
| `start_cypher.bat` | Startet Ollama, falls es nicht läuft (dann mit `OLLAMA_MAX_LOADED_MODELS=1`, siehe M0: zwei Modelle im Grafikspeicher = 10–20× langsamer). Baut die Oberfläche neu, wenn `frontend/src` neuer ist als der Build. Startet das Backend **unsichtbar** (Logs in `logs/`), wartet bis `/health` antwortet, öffnet den Browser. Läuft Cypher schon, öffnet es nur den Browser. Optionen: `-NoBrowser` (Autostart), `-Foreground` (Fehlersuche im Fenster). |
| `stop_cypher.bat` | Bittet das Backend über `POST /api/system/shutdown`, sich sauber zu beenden (Gespräche werden noch geschrieben, Lifespan-Shutdown läuft). Rückfallebenen: Prozess aus `data/cypher.pid`; bei älteren Versionen ohne PID-Datei der Prozess am Port, **aber nur, wenn seine Kommandozeile ihn als Cypher-Backend ausweist**. |
| `doctor.bat` | Prüft alles nur lesend und sagt auf Deutsch, was zu tun ist: Python/venv/Pakete, `.env`, Token-Sicherheit, Backend, Ollama, Modell, **GPU (Modell komplett im Grafikspeicher?)**, Stimme, Whisper, Oberfläche aktuell?, Datenordner + Datenbank-Integrität + letztes Backup, Cloud, Tailscale/Handy, **Wake-on-LAN (Magic Packet an der Netzwerkkarte aktiv?)**, Autostart. Exit-Code 1 nur bei echten Fehlern. |
| `backup_cypher.bat` | Zip in `backups/` (gitignored): Datenbank über die **SQLite-Online-Backup-API** (konsistent auch während Cypher läuft), Gedächtnis, Notizen, `secrets.json` falls vorhanden, Manifest. **Ohne `.env`** (könnte den Cloud-Schlüssel enthalten). `--restore <zip>` spielt zurück: nur wenn Cypher gestoppt ist, nach getipptem „JA“, und vorher wird der aktuelle Stand automatisch gesichert. |

Die `.ps1`-Dateien sind als UTF-8 **mit BOM** gespeichert. Ohne BOM liest Windows PowerShell 5.1 sie als ANSI und zerlegt die Umlaute. Die `.bat`-Dateien sind reines ASCII, weil `cmd.exe` sie in der OEM-Codepage liest.

## Weitere Bausteine

| Anforderung | Umsetzung |
|---|---|
| Frontend vom Backend | `app/api/frontend.py`: liefert `frontend/dist` aus, als Letztes registriert, damit jede API-Route gewinnt. Unbekannte Pfade bekommen `index.html` (Single-Page-App). Fehlende **Dateien** (`assets/…`, `*.js`) bleiben aber 404. `index.html`/`sw.js`/Manifest kommen mit `no-cache` (Updates greifen beim nächsten Laden), `assets/` mit Content-Hash und einem Jahr Cache. Ohne Build antwortet `/` wie bisher mit der Backend-Info. `frontend/.env.production` setzt `VITE_API_BASE_URL=` leer: Der Build spricht immer die eigene Adresse an, auch hinter `tailscale serve`. |
| Sauberes Beenden | `run.py` startet uvicorn als `Server`-Objekt und schreibt `data/cypher.pid`. `/api/system/shutdown` braucht Token **und** eine direkte Anfrage von diesem PC. |
| Autostart (optional) | `scripts/autostart.py` heisst jetzt „Cypher Autostart“ und startet `start_cypher.ps1 -NoBrowser`. `install` entfernt dabei die alte Aufgabe „JARVIS Autostart“. Die alte Aufgabe funktioniert bis dahin trotzdem: `start_jarvis.ps1` leitet nur noch an `start_cypher.ps1` weiter, ohne den alten Agenten. **Nicht von mir eingerichtet**, weil es eine dauerhafte Systemänderung mit UAC-Abfrage ist (siehe Checkliste). |
| Handy (docs/MOBILE.md) | `tailscale serve --bg 8000` gibt Cypher im Tailnet eine `https://…ts.net`-Adresse mit echtem Zertifikat. Ohne HTTPS gibt es kein Mikrofon. Das Backend bleibt auf `127.0.0.1`. Befehle und Header gegen die offizielle Doku und den Tailscale-Quellcode (`ipn/ipnlocal/serve.go`) geprüft. Doku inkl. MagicDNS + „Enable HTTPS“, Warnung zu den öffentlichen Certificate-Transparency-Logs (Gerätename vorher umbenennen), App-Installation, Abschalten, „Handy verloren?“, Fehlerbehebung. |
| Kopplung per QR | Menü → **Handy**: QR-Code + Link (`https://…ts.net/#pair=…`), sobald `tailscale serve` für Cypher aktiv ist (das Backend fragt Tailscale lesend ab, 30 s gecacht). Sonst steht dort die Einrichtungsanleitung. Den Code von Hand gibt es verborgen mit „Anzeigen“. Auf dem Handy selbst steht nur „Dieses Gerät ist gekoppelt“. QR-Erzeugung mit **segno** (BSD-3, neu in DEPENDENCIES.md), als Bild-Data-URI ohne Skriptausführung. |
| Sicherheit hinter dem Proxy | `tailscale serve` verbindet sich von `127.0.0.1` aus. Ohne Gegenmassnahme wäre das Handy für Cypher „lokal“ gewesen: Token ohne Kopplung, Herunterfahren erlaubt, und falsche Codes vom Handy hätten den PC ausgesperrt. Jetzt gilt: Anfragen mit Proxy-Headern (`X-Forwarded-For`, `X-Forwarded-Host`, `Forwarded`, `Tailscale-User-Login`) sind **nie** lokal. Die Sperre nach Fehlversuchen zählt pro weitergeleitetem Gerät. |
| Gedächtnis-Export | Menü → Gedächtnis → „Als JSON herunterladen“ (Kern + Archiv als Klartext). Das komplette Backup bleibt `backup_cypher.bat`. |
| Aufgeräumt | Tote Einstellungen des alten Agenten (`BACKEND_BASE_URL`, `AGENT_POLL_*`, `AGENT_HEARTBEAT_*`) entfernt. Eine bestehende `.env` mit diesen Zeilen startet weiter (`extra="ignore"`, also keine Migration nötig). `TAILSCALE.md` verweist auf `docs/MOBILE.md`, `check_tailscale.ps1` empfiehlt `tailscale serve`. |

## Fehler, die erst Tests und Smoke-Tests gefunden haben

1. **Frische Installation ohne Stimme.** Das Archiv der Piper-Stimme entpackt nach `vits-piper-de_DE-thorsten-medium/`, Cypher erwartet `de_DE-thorsten-medium/`. Auf deinem PC war der Ordner offenbar damals von Hand umbenannt. Jede Neuinstallation hätte ohne Sprachausgabe gestartet. Jetzt: Entpacken in einen Temp-Ordner (mit `filter="data"` gegen Pfad-Tricks), dann an den richtigen Ort verschieben.
2. **Backup ging unter Windows nie.** `with sqlite3.connect(...)` committet nur, schliesst aber nicht. Der Temp-Ordner blieb gesperrt, also scheiterte jedes Backup. Behoben mit `contextlib.closing`.
3. **Restore konnte sein eigenes Archiv überschreiben**, wenn die Sicherheitskopie in derselben Sekunde entstand (gleicher Dateiname → „Bad CRC-32“). Jetzt haben Backups eindeutige Namen, die Sicherheitskopie heisst `…-vor-restore.zip`.
4. Die Oberfläche lieferte für eine fehlende JS-Datei `index.html` mit Status 200. Nach einem Update hätte der Browser HTML als Skript ausgeführt.
5. `setup` meldete „Fertig“ trotz fehlender Stimme. Die `.bat`-Dateien verschluckten wegen `pause` den Exit-Code.
6. `stop_cypher` konnte die alte, laufende Instanz nicht beenden (keine PID-Datei) und wartete 15 s umsonst.
7. `backup_cypher.bat --restore backups\…` suchte relativ zu `backend\`.

## Tests

- **Backend: 592 grün** (vorher 563). Neu:
  - `test_m7_ops.py`: Auslieferung der Oberfläche (Cache-Header, Fallback, API nie überdeckt, kein Datei-Leck, fehlende Assets = 404), Shutdown (Token, 409 ohne `run.py`, 403 von anderem Gerät und über Proxy), Backup (Inhalt, Roundtrip mit Sicherheitskopie, unsichere Pfade, fremdes Zip, eindeutige Namen), Tailscale-Parser, Proxy-Erkennung, Kopplung mit/ohne Tailscale, Sperre pro Gerät, Doktor offline, Prozess-Erkennung für den Stopp
  - `test_piper_download.py`: Umbenennen, unvollständiges Archiv, unsichere Pfade
  - `test_autostart.py`: neuer Aufgabenname, `-NoBrowser`, alte Aufgabe wird zuerst entfernt
- **Vitest: 85 grün** (+4: Handy-Panel mit QR / ohne Tailscale / auf dem Handy, Gedächtnis-Export).
- **Playwright (Edge): 5 grün.** tsc, ESLint und Prettier sauber.

## Smoke-Test (echt ausgeführt)

| Was | Ergebnis |
|---|---|
| Laufende alte Instanz (ohne PID-Datei) mit `stop_cypher.bat` beenden | über die Port-Erkennung beendet |
| `start_cypher.bat` mit veralteter Oberfläche | baut neu, startet unsichtbar (kein Fenster), „Cypher läuft: http://127.0.0.1:8000“ |
| Start ohne Neubau | **6,6 s** bis `/health` (Neustart später: 5,8 s) |
| `/api/health` | alles ok, `qwen3.5:9b` **100 % GPU** (RX 9070 XT) |
| `http://127.0.0.1:8000/` | Oberfläche mit `no-cache`, Menü → Handy am PC und bei 375 px ohne seitliches Scrollen |
| `stop_cypher.bat` | **2,2 s**, Log: „Shutdown requested“ → „Application shutdown complete“, PID-Datei entfernt |
| `backup_cypher.bat` während Cypher läuft | 38 KB: Datenbank, Gedächtnis, 2 Notizen, Manifest. Restore wird verweigert, solange Cypher läuft. |
| `doctor.bat` | keine Fehler, 1 Hinweis (alte Autostart-Aufgabe) |
| **Frische Kopie** (nur Repo-Dateien, ohne venv/node_modules/Modelle/Daten) | 1. Lauf deckte Fehler 1 + 5 auf. 2. Lauf: `setup.bat` komplett grün → `start_cypher.bat` (Port 8010) gesund nach **5,3 s**, Token selbst erzeugt, Oberfläche ausgeliefert → „Wie spät ist es?“ → „Es ist 18:25 Uhr am Donnerstag, 8. Oktober 2026.“ in **0,96 s** → `stop_cypher.bat` in 1,9 s |

Das `setup` lief mit warmem pip-Cache und schon installierten Ollama-Modellen. Auf einem neuen PC dauert es durch die Downloads entsprechend länger.

Nebenbei beendet: zwei Prozesse des alten PC-Agenten vom heutigen Login (alte Autostart-Version). Sie schickten alle 15 s Heartbeats an Endpunkte, die es seit M5 nicht mehr gibt. Beim nächsten Login startet er nicht mehr.

## Manuelle Checkliste (braucht dich)

- [ ] `start_cypher.bat` per **Doppelklick**: Browser geht auf, das Konsolenfenster schliesst sich, kein Fenster bleibt offen.
- [ ] Autostart umstellen (UAC-Abfrage bestätigen): `backend\.venv\Scripts\python.exe scripts\autostart.py install`, dann `… status`. Danach ab- und wieder anmelden: Cypher läuft ohne Browserfenster, `doctor.bat` zeigt ✓ Autostart.
- [ ] Startet Ollama bei dir über das Tray-Symbol, setzt das Startskript `OLLAMA_MAX_LOADED_MODELS` nicht. Dann einmal als Benutzer-Umgebungsvariable setzen: Windows-Suche „Umgebungsvariablen“ → Neu → `OLLAMA_MAX_LOADED_MODELS` = `1`, danach Ollama neu starten.
- [ ] Wiederherstellung einmal üben: `stop_cypher.bat` → `backup_cypher.bat --restore backups\<datei>.zip` → `JA` → `start_cypher.bat` → Gedächtnis ist da.
- [ ] Handy: die Prüfliste am Ende von `docs/MOBILE.md` (Tailscale ist auf diesem PC nicht installiert, daher nicht live getestet).
- [ ] Wake-on-LAN (optional): MAC in `WAKE_ON_LAN_MAC` eintragen, `doctor.bat` prüft die Netzwerkkarte.

## Offene Punkte

- **Tailscale/Handy nicht live getestet.** Abgesichert durch Tests (Proxy-Erkennung, Kopplungslink, QR, `wss://` unter HTTPS) und die Prüfliste.
- `frontend/.env` enthält noch das veraltete `VITE_USER_TOKEN`. Es wird ignoriert, aber bei jedem Build gewarnt. Die Zeile kannst du löschen (deine Datei, nicht im Repo).
- `npm ci` meldet, dass das Installationsskript von esbuild nicht freigegeben ist (npm 11 „allowScripts“). Der Build funktioniert trotzdem, weil das passende esbuild-Binary als eigenes Paket kommt.
- `MESSAGE_MAX_RETRIES` und das Feld `message_max_retries` in `/api/settings` stammen aus der alten Pipeline und werden nicht mehr genutzt. Das räume ich in M8 auf.
- README beschreibt noch den alten Start. Wird in M8 neu geschrieben.
