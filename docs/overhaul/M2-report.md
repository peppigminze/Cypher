# M2-Report – Sicherheit & Tool-System

## Was geändert wurde

### Zugang (Auth)
- **Kein Token mehr im Frontend-Bundle** (AUDIT K5). `VITE_USER_TOKEN` wird nirgends mehr gelesen;
  Nachweis: Produktions-Build enthält den Token nicht (0 Treffer).
- Token-Quelle: Wert aus `.env` (wenn gesetzt und kein Platzhalter) **oder** beim ersten Start
  generiert (`data/secrets.json`, gitignored). Platzhalter wie `change-me-user-token` werden nie
  mehr als Zugang akzeptiert.
- **Lokale UI ohne Token-Eingabe:** `GET /api/auth/session` gibt den Token nur an Anfragen von
  diesem PC heraus – Loopback-Peer **und** lokaler Host-Header (gegen DNS-Rebinding) **und**
  `Sec-Fetch-Site` same-origin/same-site **und** erlaubte Origin. Andere Geräte koppeln einmalig
  (Link `#pair=…` oder Code im neuen Kopplungs-Bildschirm).
- Neue Startregel: kurzer (< 24 Zeichen) Token + Bindung über localhost hinaus → Start verweigert.
- **Rate-Limiting** (Token-Bucket je IP): Chat 30/min, Audio 60/min, Freigaben 30/min,
  Session 20/min; 10 Fehlversuche in 5 min → 5 min Sperre (auch für den richtigen Token).

### Tool-System
- **Plugin-Registry:** Tools sind Klassen mit `name`, pydantic-`Args`, `risk`, `execute()` und
  registrieren sich per `@register_tool`; `app/tools/catalog.py` findet alle `*_tools`-Module
  automatisch (49 Tools). Alle Bestands-Tools migriert (Risk-Tier statt altem Flag, Auto-Registrierung).
- **Risk-Tiers:** `read_only` / `control` (harmlose PC-Steuerung, läuft sofort) / `write`
  (Freigabe) / `destructive` (Freigabe + Warnung) / `blocked`. Pro Aufruf veränderbar
  (`risk_for`): z. B. `write_file` neu = write, überschreiben = destructive; `read_file`,
  `read_clipboard`, `describe_screen` brauchen im **Cloud-Modus** eine Freigabe (Daten verlassen den PC).
- **Gehärteter Executor** – einziger Ausführungspfad, auch für den alten Pipeline-Pfad:
  Argument-Validierung, Risk-Durchsetzung (write/destructive ohne Freigabe-ID → Verweigerung,
  auch wenn ein Aufrufer das Gate vergisst), Taint-Sperre, Timeout, Untrusted-Markierung.
- **Audit-Log (SQLite `tool_audit`):** jede Entscheidung (auto, approval_requested, approved,
  rejected, expired, blocked, invalid, needs_approval, tainted_blocked) mit Zeit, Request-ID,
  Quelle, Provider, Argumenten (Secrets geschwärzt), Dauer, Ergebnis-Vorschau. `GET /api/audit`.
- **Freigaben:** Karte enthält jetzt Zusammenfassung auf Deutsch, Risikostufe und Ablaufzeit;
  `GET /api/approvals`; abgelaufene Freigaben werden protokolliert. **Bugfix H1:** Nach „Nein"
  antwortet Cypher fest „Okay, ich habe es nicht ausgeführt." statt „Erledigt.".
- **Sandbox** (`app/security/paths.py`): erlaubte Wurzeln, vollständige Auflösung von Symlinks
  **und Junctions**, Sperre für Windows-/Programmordner, AppData (außer Temp), `.ssh`/`.aws`/…,
  Geheimnis-Dateien (`.env`, `*.pem`, `*.kdbx`, `*.db`, …) und Cyphers eigene Daten; keine
  NTFS-Streams, Gerätenamen oder UNC-Pfade; Lese-Limit **vor** dem Lesen, Binärdateien abgelehnt,
  Schreib-Limit, atomares Schreiben.
- **Prompt-Injection-Hygiene:** externe Inhalte werden zwischen Markierungen gesetzt, darin
  enthaltene Markierungen/Chat-Rollen-Tags neutralisiert; Systemregel „Daten, keine Anweisungen";
  **Taint-Tracking**: nach externem Inhalt im selben Zug verweigern `save_memory` und
  `write_clipboard` den Dienst.

### Neue / überarbeitete Tools
| Tool | Tier | Umsetzung |
|---|---|---|
| `web_search` | read_only | DuckDuckGo HTML per POST (GET wird als Bot geblockt), optional SearXNG |
| `fetch_url` | read_only | Redirects manuell, jeder Hop geprüft, IP-Pinning + SNI gegen DNS-Rebinding, blockt jetzt auch **100.64.0.0/10 (Tailnet)** |
| `set_volume` / `get_volume` | control / read_only | Windows Core Audio (pycaw) |
| `media_control` | control | Windows-Mediensitzung: echtes play/pause/next/previous (kein Toggle-Raten), Fallback Medientaste |
| `get_now_playing` | read_only | Titel/Interpret/Status |
| `open_app` | control | **nur** `APP_WHITELIST` (Name → exe/URI), kein Befehlstext vom Modell |
| `set_timer` / `list_timers` / `cancel_timer` | control | auf Task-Modell; Scheduler wacht jetzt präzise auf (statt 20-s-Takt) und meldet über Event-Bus |
| `read_clipboard` / `write_clipboard` | read_only* / control | Win32 per ctypes |
| `save_note` / `list_notes` | write / read_only | Markdown pro Tag in `data/notes/` |
| `get_weather` | read_only | Open-Meteo (ohne Key) |
| `describe_screen` | read_only* | Screenshot → **lokales** Vision-Modell (qwen3.5:9b hat Vision, kein Extra-VRAM) |

\* Freigabe nötig, solange der Cloud-Provider aktiv ist.

## Was getestet wurde

**Automatisch:** 417 Backend-Tests (+86 in M2), 68 Frontend-Tests (+7) grün.
Neu u. a.: Token-Generierung/Stabilität/Platzhalter-Abweisung, lokale Session vs. Cross-Site/
DNS-Rebinding/Remote-Peer, WebSocket-Origin, Rate-Limit + Sperre, Sandbox (Traversal, **echte
NTFS-Junction nach außen**, Streams, Gerätenamen, UNC, Geheimnis-Dateien, AppData, Systemordner,
Größenlimits, Binärdateien), Framework (Schema, Validierung, Tiers, Katalog, Executor, Timeout,
Audit inkl. Schwärzung), **7 Prompt-Injection-Tests** mit einem absichtlich „gehorsamen"
Fake-Modell (Webseite → Memory, Datei → Zwischenablage, injizierter Dateischreibvorgang → nur
Freigabekarte, Markierungs-Ausbruch neutralisiert, direkter Nutzerwunsch funktioniert weiter,
ehrliche Ablehnung), neue Tools (alle OS-Zugriffe gemockt), Freigabe-API inkl. Audit-Kette.

**Real (echte Dienste / echtes Modell qwen3.5:9b):**

| Test | Ergebnis |
|---|---|
| „Wie wird das Wetter morgen in Bern?" | `get_weather` → „Morgen in Bern ist Regen zu erwarten … zwischen 10,6 und 15,1 Grad" (3,4 s) |
| „Was läuft gerade für ein Song?" | `get_now_playing` → „Just A Lil Bit" von 50 Cent auf Spotify (1,6 s) |
| „Stell die Lautstärke auf 8 Prozent." | `set_volume(level=8)` (bewusst gleicher Wert wie vorher) |
| „Stell einen Timer auf 5 Sekunden." | `set_timer(5)`, Klingelzeit genannt |
| „Such im Internet nach … Migros Bahnhof Bern" | `web_search` (echte DuckDuckGo-Treffer) → Öffnungszeiten genannt |
| „Schreib eine Notiz: Abnahmetest M2 erfolgreich." | **Freigabekarte** → Ja → `data/notes/2026-10-07.md` geschrieben; Audit: `approval_requested` → `approved` |
| `fetch_url` echte HTTPS-Seite | ok (IP-Pinning + SNI); `127.0.0.1` und `100.100.100.100` blockiert |
| Lokale Session im echten Browser | Token ohne Eingabe; Anfrage von `https://example.com` aus erreicht den Server gar nicht |
| Live-Injection (`eval/injection_live.py`, 2 Läufe × 3 Fälle) | Modell hat **0** eingeschleuste Aktionen versucht |

## Seiteneffekte / Hinweise
- **Bestandstests drückten echte Medientasten** (vor M2 jeder Testlauf; heute dadurch mehrmals
  Spotify umgeschaltet/Titel gewechselt). Jetzt komplett gemockt. Ein weiterer Bestandstest rief
  `open_application("shutdown /s /t 0")` echt auf und verließ sich nur auf die Sperrliste – jetzt
  mit Popen-Mock abgesichert.
- `frontend/.env` enthält noch `VITE_USER_TOKEN` – wird ignoriert (Build warnt), kann gelöscht werden.
- Eine Test-Notiz liegt in `data/notes/2026-10-07.md`.

## Offen / bewusst verschoben
- Freigaben überleben keinen Backend-Neustart (Absicht: Kontext ginge verloren); jeder Schritt
  steht im Audit-Log.
- Kopplungs-Link im Menü und schöne Freigabekarte → M6 (Backend-Endpunkte stehen).
- `remember`/`forget` als eigene Memory-Tools → M4 (heute `save_memory` mit klarer Regel:
  ohne Rückfrage nur bei „merk dir …" und ohne fremde Inhalte im Zug).
- Status-Badge „Getrennt" hängt noch am alten Agent-Heartbeat → M6.
