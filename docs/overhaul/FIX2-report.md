# FIX2 – Folge-Zuhörfenster, lokale Schriften, volle PC-Steuerung

Branch `fix/followup-fonts-pccontrol`, drei Punkte. Punkt 1 und 2 sind je ein Commit, Punkt 3 besteht aus mehreren (siehe Abschnitt 3).

## 1. Folge-Zuhörfenster

### Ausgangslage (belegt)

`VoiceSession._on_playback_done` (`backend/app/voice/session.py`) setzte die Sitzung nach jeder Antwort sofort zurück auf `wake_listening`. Jede Folgefrage brauchte also wieder „Hey Cypher“.

### Umsetzung

| Anforderung | Umsetzung |
|---|---|
| Fenster nach jeder Antwort, 10 s | `_finish_turn` merkt sich „Antwort gesprochen“. Das Fenster öffnet erst bei `playback_done`, also wenn der Browser die Wiedergabe **wirklich** beendet hat, nicht wenn die Generierung fertig ist. Nach jeder Folgeantwort startet es neu. |
| Äusserung über die Fensterkante | `SpeechStart` hält den Zähler an (`{"type":"followup","active":false}`). Ein Zähler offener Äusserungen verhindert, dass der Timer zwischen Sprechende und Verarbeitung zuschlägt (diese Lücke gab es vorher auch beim normalen Zuhören). |
| Ablauf ohne Sprache | Timer **nur im Backend** (`_timeouts`, jetzt 0,1-s-Takt) → zurück zu `wake_listening`. Bei Verbindungsabbruch startet die neue Sitzung immer im Ruhezustand. |
| „Cypher, schlaf“ | `app/voice/sleep.py`: Name phonetisch wie beim Aktivierungswort (Schwelle 0,85), dazu genau „schlaf“/„schlafen“. Alle übrigen Wörter müssen Füllwörter sein, deshalb lösen „Ich habe schlecht geschlafen“ oder „Super schlaf“ nicht aus. Antwort „Gut.“, geht nie ans LLM. Funktioniert auch als „Hey Cypher, schlaf“ und während Cypher spricht. |
| Freigabekarten | unverändert. Während `confirming` öffnet sich kein Fenster, erst nach der Entscheidung und der Antwort darauf. |
| Echo-Schutz | Nach der Wiedergabe wird das Mikrofon 0,35 s ignoriert (Ausklang). Danach greift der bestehende Textabgleich: Was Cypher gerade gesagt hat, ist kein Befehl, und das Fenster läuft weiter. |
| Unterbrechen | unverändert. Tippen während des Fensters beendet es. |
| Einstellung | `FOLLOWUP_WINDOW_SECONDS` (Standard 10, 0 = aus) in `.env.example`. Im Menü unter Einstellungen → „Weiter zuhören“ (aus/5/10/15/20/30 s), gespeichert in der Datenbank; dieser Wert hat Vorrang (`GET/PUT /api/settings/voice`). |
| UI | Kein neuer Hauptzustand: `listening` hat jetzt den Unterzustand `off → open → paused` (`voiceMachine.ts`, `FOLLOWUP_FLOW`, explizite Tabelle mit Tests). Die Blase bekommt ein eigenes, ruhiges Aussehen (türkis) mit einem Ring, der sich im Uhrzeigersinn leert, dazu den Text „Ich höre weiter zu - frag einfach weiter.“. Die Antwort bleibt lesbar, bis du sprichst. Die Restzeit kommt immer vom Server. |

### Vom Live-Test gefundene Fehler (behoben)

1. **Ein Atemzug, länger als das Fenster des Aktivierungsworts:** Bei „Hey Saifer, wie spät ist es?“ hörte Whisper-tiny in seinen 2,5 s nur „Hi Cypher.“. Die Sitzung wartete dann auf einen Befehl, der **schon gesprochen** war, und die Frage ging verloren. Das bestand schon vor FIX2 und trat zufällig auf. Jetzt wird bei Äusserungen, die länger als das Fenster sind, die ganze Äusserung mit Whisper-small transkribiert.
2. **„Hey Cypher“ im Fenster** ging als Frage ans LLM (Antwort „Ja?“). Jetzt wird ein vorangestelltes Aktivierungswort entfernt. Ein alleinstehendes „Hey Cypher“ heisst nur: zuhören.
3. **„Saifer, schlaf.“** wurde mit der Testsprache als „Cypher Klaw.“ erkannt. Im Wake-Modus nennt der Whisper-Prompt für Befehle jetzt auch „Cypher, schlaf.“. Gemessen: „Cypher, schlaf.“ wird dann richtig erkannt. 10 typische Folgefragen wurden mit und ohne den Zusatz gleich transkribiert (1 Abweichung, bei einem Satz, der in beiden Fällen falsch war). Rechenzeit unverändert (1,24 s / 1,24 s).

### Tests

- Backend: **+32** (`tests/test_voice_followup.py`). Das Fenster startet nach Wiedergabe-Ende und nicht nach der Generierung. Es verlängert sich nach einer Folgeantwort und läuft ab. Eine Äusserung über die Kante wird nicht abgeschnitten. Getestet sind ausserdem: Fenster aus bei 0 und ausserhalb des Wake-Modus, Schlaf-Befehl positiv (7 Varianten) und negativ (8 Sätze), Echo im Fenster, Barge-in im Fenster, Freigabe vor dem Fenster, Tippen beendet das Fenster, Einstellung über die API, sowie die drei Funde oben. Gesamt **629 grün**.
- Frontend: Vitest **96** (+11: Unterzustands-Tabelle, Reducer, Ring-Anteil, Einstellung). Playwright **6** (+1: Fenster sichtbar, Antwort bleibt lesbar, schliesst wieder).

### Smoke-Test (echt, isolierte Instanz, Piper-Stimme über den echten WebSocket)

| Prüfung | Ergebnis |
|---|---|
| Fenster öffnet nach Wiedergabe-Ende | 0,002 s nach `playback_done` |
| Folgefrage ohne Aktivierungswort („Und welcher Wochentag ist heute?“) | „Heute ist Freitag.“, kein Wake-Check, 1,85 s bis zum ersten Audio |
| Fenster startet nach der Folgeantwort neu | ja |
| Ablauf in Stille | nach 10,06 s zurück zum Aktivierungswort |
| „Saifer, schlaf.“ im Fenster | erkannt als „Cypher, schlaf.“, „Gut.“, kein LLM-Aufruf, schläft nach 2,0 s (inkl. Wiedergabe von „Gut.“) |
| Echo: Antwort „Hey, ich bin Cypher.“ läuft zurück ins Mikrofon | keine Selbstunterbrechung, kein neuer Turn im Fenster, Fenster schliesst von selbst |
| Unterbrechen, Aktivierungswort, Gegenprobe mit Gerede | unverändert grün |

**Latenz:** Während dieser Läufe lief VALORANT auf dem PC (CPU und GPU geteilt). „Hey Cypher, wie spät ist es?“ kam auf 2,7–2,8 s statt ~2,0 s gestern. Ein A/B-Vergleich mit dem Sitzungscode **vor** FIX2 unter derselben Last ergab dasselbe (2,77 / 2,83 s), die Verlangsamung kommt also von der Last und nicht von FIX2. Einmal hörte tiny die synthetische Stimme als „Hals einfach“ und das Aktivierungswort griff nicht. Das ist die bekannte Streuung der Testsprache.

## 2. Schriften lokal

### Ausgangslage (belegt)

`frontend/index.html` lud Space Grotesk, Inter und Share Tech Mono über `fonts.googleapis.com`/`fonts.gstatic.com`. Jedes Öffnen der Oberfläche schickte also eine Anfrage an Google, und offline fielen die Schriften auf Systemschriften zurück.

### Umsetzung

- **Nur die Schriftdateien** übernommen: 7 woff2-Dateien (Latin-Teilmenge, nur die verwendeten Schnitte) aus den @fontsource-Paketen 5.3.0, dazu die drei OFL-Lizenztexte, unter `frontend/src/assets/fonts/`. Die Pakete selbst stehen komplett (auch ihr CSS) unter OFL. Deshalb ist **kein** npm-Paket Abhängigkeit, und die `@font-face`-Regeln in `src/styles/fonts.css` sind eigener Code (OFL nur für Fonts, Code bleibt MIT/Apache/BSD).
- Die Latin-Teilmenge reicht für Deutsch (ä ö ü ß, „“, €). Zeichen ausserhalb (z. B. →) nimmt wie bisher die Systemschrift.
- Google-Links aus `index.html` entfernt. Vite versieht die Dateien mit Hash, der Service Worker nimmt sie in den Precache (`globPatterns` um `woff2` ergänzt).
- `docs/DEPENDENCIES.md`: Abschnitt „Schriften“ mit Lizenz und Urhebern, Ausnahme-Regel für OFL-Fonts oben notiert.

### Grösse

| | vorher | nachher |
|---|---|---|
| Schriftdateien im Build | 0 (von Google) | 7 Dateien, **125 KB** (Inter 3 × ~24 KB, Space Grotesk 3 × ~13 KB, Share Tech Mono 13,5 KB) |
| Precache des Service Workers | 9 Einträge, 247,6 KiB | 16 Einträge, 374,7 KiB |
| CSS | 27,0 KB | 29,5 KB (`@font-face`-Regeln) |

### Tests (Playwright, `e2e/fonts.spec.ts`)

1. **Keine Anfrage an Google:** Alle Netzwerkanfragen beim Öffnen werden mitgeschrieben. Keine geht an `google`/`gstatic` oder überhaupt an eine andere Adresse als `localhost`. Die woff2-Datei kommt vom eigenen Server.
2. **Offline:** Erster Besuch installiert den Service Worker, dann Netzwerk aus (`context.setOffline(true)`) und Neu laden. Alle drei Schriften sind `loaded`, und Text in ihnen ist nachweislich anders breit als in der Ersatzschrift (also wirklich sichtbar, nicht nur „geladen“).

Ein erster Testlauf war rot, weil der Vergleichstext Gewicht 400 nutzte. Space Grotesk gibt es erst ab 500, also nahm der Browser die Ersatzschrift. Behoben im Test (gleiches Gewicht wie die geladene Schrift), kein Fehler in der App.

### Smoke-Test (echt)

Die laufende Instanz (`http://127.0.0.1:8000`) im eingebauten Browser: **keine** Anfrage mit „google“, die woff2-Dateien kommen von `127.0.0.1:8000` (200). `document.fonts` meldet Inter 400, Space Grotesk 700 und Share Tech Mono 400 als geladen. Das Aussehen ist unverändert.

## 3. Volle PC-Steuerung mit abschaltbarer Sicherheitsabfrage

### Ausgangslage (belegt)

Vor Punkt 3 (Stand `678719f`, Standardwerte) bot Cypher dem Modell **22 Werkzeuge** an. Keines davon war für Dateien, Fenster, Prozesse, PowerShell, Energie oder Software. `open_app` startete nur die 8 Kurznamen aus `APP_WHITELIST`. Die Datei-Werkzeuge galten nur in `ALLOWED_DIRECTORIES` (Standard leer, also aus). Der alte Terminal-Befehl war `destructive` und wurde nicht angeboten. Das Risiko war eine feste Stufe pro Werkzeug, nicht pro Aufruf.

### Umsetzung

| Teil | Umsetzung |
|---|---|
| Werkzeuge | **36 statt 22** angeboten. Neu bzw. erweitert: `run_command` + `command_status` (PowerShell, Vorder-/Hintergrund), `open_app` (jede installierte App aus dem Startmenü, Webseite, URI, Datei), `windows`, `processes`, `list_files`, `read_file`, `write_file`, `file_op` (alle Laufwerke, Löschen = Papierkorb), `input_control` (Tastatur/Maus), `screenshot`, `power`, `set_brightness`, `software` (winget), `network_status` |
| Klassifizierer | `app/security/classifier.py`: feste Regeln für PowerShell-Zeilen (Ketten, Skriptblöcke, Aliase, `-EncodedCommand`), Pfade, Programme, URIs, Prozesse. Unbekanntes wird `critical`. Stufen `safe < control < write < critical < blocked` |
| Policy | `app/security/policy.py`: fremder Inhalt + ab `write` → fragt immer. Rote Linie → fragt immer. `critical` → fragt, solange die Abfrage an ist. Sonst läuft es |
| Schalter | Menü → Einstellungen, Standard an. Aus nur in zwei Schritten (Warnung, „Nur für diese Sitzung“ / „Dauerhaft“). Nur aus der lokalen Oberfläche änderbar (`PUT /api/security/critical-confirm`: Token, Loopback, lokaler Host, keine Proxy-Header, `Sec-Fetch-Site`). Jede Änderung im Audit-Log, Kopfzeile ⚠ ABFRAGE AUS, Doktor warnt |
| Sprache | Kritische und Rote-Linie-Karten nur mit „Ja, ausführen“. Whisper bekommt die Phrase als Prompt, ein einfaches „Ja“ bekommt einen Hinweis. Das Mikrofon ist stumm, solange die Frage läuft |
| Karte | genaues Werkzeug und Argumente (z. B. der PowerShell-Befehl), Stufe, Grund des Klassifizierers, Art (`critical` / `red_line` / `untrusted`) |
| PowerShell | `-EncodedCommand`, UTF-8, Fehler aus CLIXML lesbar, Unterbrechen oder Zeitlimit beendet den ganzen Prozessbaum, Cyphers Token nicht in der Kind-Umgebung, jeder Befehl in `command_logs` |
| Adminrechte | Cypher erhöht sich nie (`-Verb RunAs` ist `blocked`). Läuft der Prozess erhöht, verweigert `run_command`. Braucht ein Befehl Adminrechte, erklärt Cypher, wie du es selbst als Administrator ausführst |
| App-Begriffe (Abschluss) | Alias-Tabelle `app/pc/app_aliases.py` mit Vorrang vor dem Startmenü, konfigurierbar über `APP_ALIASES`. Kurzer „öffne X“-Satz ohne LLM. Unbekannt oder mehrdeutig → Rückfrage |
| Kontextfenster | `LOCAL_LLM_NUM_CTX` 8192 → **12288**, siehe Messung |

### Commits

| Commit | Inhalt |
|---|---|
| `6145cbb` | WIP: Klassifizierer, Policy, PC-Werkzeuge, Sprachbestätigung, Schalter, HUD, Tests |
| `c8e2bd4` | PowerShell-Tests unabhängig von Adminrechten des Testlaufs |
| `ba59568` | Alias-Tabelle für App-Begriffe |
| `fc930b4` | Doku: README, ARCHITECTURE, `.env.example`, DEPENDENCIES, ACCEPTANCE, dieser Abschnitt |
| `8ad7ab1` | LF für das Frontend erzwingen (Fund aus dem Prettier-Lauf) |
| `5efa1d9` | Prüfung „Schalter nur über die lokale Oberfläche“ |
| Abschluss-Commit | Teststände und offene Punkte (unten) |

### Funde beim Abschluss (behoben)

1. **Tests hingen an den Rechten des Testlaufs.** Nachgestellt mit `IsUserAnAdmin() = 1`: Nicht nur ein Test, sondern **10 von 13** in `test_shell_tools.py` scheiterten, weil `run_command` im erhöhten Prozess richtig ablehnt. Die übrigen 781 Backend-Tests blieben grün, es gab also keine weitere Abhängigkeit. Jetzt wird die Prüfung in diesen Tests durch eine Attrappe ersetzt. Zwei neue Tests belegen, dass ein erhöhter Prozess weder im Vorder- noch im Hintergrund etwas startet oder protokolliert. Gegenprobe: Ohne die Prüfung in `shell_tools.py` werden beide rot.
2. **„Mach mir den Editor auf“ → VS Code** (Fund aus dem Smoke-Test des Nutzers, `qwen3.5:4b`). Ursache: Das Werkzeug löste „Editor“ schon richtig auf Notepad auf. Bis Punkt 3 nannte die Werkzeugbeschreibung aber „Editor“ ausdrücklich als Beispiel („z. B. Spotify, Rechner, Editor …“), und in M5 übergab das Modell `editor`/`notepad`. Seit Punkt 3 stand dort nur noch „z. B. 'Discord', 'Rechner'“. Das Modell wählte den App-Namen also selbst. Jetzt geht ein kurzer „öffne X“-Satz mit Alias oder exaktem App-Namen ohne LLM an `open_app`, mit dem Wort des Nutzers. Gegenprobe: Ohne den Schnellweg wird der Test rot (das Skript-Modell hätte „Visual Studio Code“ geöffnet). Live nachgeprüft ist das noch nicht (ACCEPTANCE Nr. 14).
3. **Unbelegte Messung im Code.** `config.py` behauptete „measured +0.14 GB VRAM and no extra latency“ mit Verweis auf diesen Bericht. Eine solche Messung gibt es hier nicht. Der Kommentar nennt jetzt nur das nachgerechnete Budget.

### Messung: Kontextfenster

Schätzung mit `app/memory/budget.py` (3,5 Zeichen pro Token), Standardwerte, je aus `git archive` des Stands:

| | vor Punkt 3 (`678719f`) | jetzt |
|---|---|---|
| angebotene Werkzeuge | 22 | 36 |
| Werkzeug-Definitionen | 2305 Tokens | 3991 Tokens |
| Budget gesamt (inkl. Verlauf, Gedächtnis, Webseite, Antwort-Reserve) | 7468 | 9154 |
| passt in 8192? | ja | **nein** → `num_ctx` 12288 |

**Nicht gemessen:** Grafikspeicher und Latenz mit 12288 sowie eine neue Befehls-Suite mit 36 Werkzeugen. Die M5-Messungen unter `docs/overhaul/bench/` bleiben die Referenz und wurden nicht überschrieben.

### Tests

Alle Tests laufen gegen Attrappen. Echt sind nur harmlose PowerShell-Befehle (`Write-Output`, `Start-Sleep`, `ping 127.0.0.1`), ein eigener Dummy-Prozess, Registry- und Netzwerkstatus lesen.

| Datei | Tests | Inhalt |
|---|---|---|
| `tests/test_classifier.py` | 116 | 101 PowerShell-Befehle (harmlos, ändernd, kritisch, verschleiert), Pfade, Prozesse, Öffnen-Ziele, rote Linie |
| `tests/test_security_policy.py` | 20 | Standard an, Sitzung/dauerhaft, rote Linie, Taint, Audit, nur lokale Oberfläche (Handy, Skript, fremde Seite, DNS-Rebinding), kein Werkzeug, Injection aus Datei, Sprach-Turn |
| `tests/test_shell_tools.py` | 15 | echte PowerShell: UTF-8, Exit-Code, Zeitlimit, Unterbrechen beendet den Prozessbaum, Hintergrund, Adminrechte, erhöhter Prozess |
| `tests/test_voice_critical.py` | 5 | nur „Ja, ausführen“, „Ja“ allein reicht nicht, Nein, Mikrofon stumm, Klick |
| `tests/test_app_tools.py`, `tests/test_file_tools.py` | 25 + 12 | Apps, Fenster, Prozesse, Energie, Software, Eingabe, Dateien (Papierkorb) |
| `tests/test_app_aliases.py` | 39 | Alias-Auflösung, Vorrang, exakter Name, Unbekanntes, Konfiguration, Standardbrowser, Freigabe bleibt |
| `frontend/src/components/__tests__/SafetyPrompt.test.tsx` | 9 | Schalter, Warnung, Fehlermeldung, HUD, Karte mit Befehl und Phrase |
| `frontend/e2e/safety.spec.ts` | 1 | Ausschalten nur nach Warnung, HUD-Hinweis, wieder einschalten |

Teststand der vollen Läufe: siehe [Abschluss](#abschluss).

### Smoke-Test mit echten Aktionen

**Vom Nutzer von Hand durchgeführt**, nicht von Claude und von keinem Skript. Ein Ergebnis ist bekannt: der Fund „Editor → VS Code“ (oben, behoben). Die übrigen Prüfungen und die Nachprüfung stehen als Checkliste mit leeren Ergebnisfeldern in [ACCEPTANCE.md](../ACCEPTANCE.md#pc-steuerung-und-sicherheitsabfrage-fix2-vom-nutzer-durchgeführt).

### Prüfung: Schalter nur über die lokale Oberfläche (ohne Ausführung)

Geprüft wurde der Code und die Einstufung durch den Klassifizierer. Kein Versuch lief gegen eine laufende Instanz.

| Frage | Befund |
|---|---|
| Wer schreibt den Schalter? | Nur `policy.set_critical_confirm`, und das ruft im ganzen Backend nur `PUT /api/security/critical-confirm` auf (Suche über `app/`). `settings_store` schreibt nur zwei andere, feste Schlüssel (`active_llm_provider`, `voice.followup_window_seconds`) |
| Was verlangt der Endpunkt? | Token, Absender Loopback, lokaler `Host` (gegen DNS-Rebinding), keine Proxy-Header (Handy über `tailscale serve`), `Origin` lokal und erlaubt, `Sec-Fetch-Site` = `same-origin`/`same-site` |
| Werkzeuge? | Kein Tool-Modul enthält `set_critical_confirm` oder den Schlüssel (statischer Test). Kein Werkzeug wird dem Modell dafür angeboten |
| Sprache? | Ein Sprach-Turn erreicht nur die Engine, die keinen Weg zum Schalter hat |
| Datenbank direkt? | Ein Pfad auf `jarvis.db` in einem Befehl oder Dateiwerkzeug ist rote Linie (`sqlite3.exe …jarvis.db`, `Set-Content`, `Copy-Item`, `write_file`: alle `cypher_secrets`) |
| Cyphers API über PowerShell/`open_app`? | rote Linie (`Invoke-RestMethod`/`Invoke-WebRequest` auf `127.0.0.1`/`localhost`, `open_app` auf eine lokale Adresse) |

Tests, alle grün: `test_api_local_ui_can_switch`, `test_api_refuses_everything_but_the_local_ui` (Skript ohne Browser-Header, fremde Webseite, Handy über Proxy, DNS-Rebinding), `test_api_needs_the_token`, `test_no_tool_can_change_the_setting`, `test_injection_from_a_file_cannot_switch_it_off`, `test_injection_works_even_with_the_prompt_already_off`, `test_a_voice_turn_cannot_switch_it_off` (10 Fälle), dazu Vitest `SafetyPrompt.test.tsx` (9, u. a. Ablehnung vom Handy) und Playwright `safety.spec.ts`.

Ergebnis: Das hält für Handy, fremde Webseiten, DNS-Rebinding, Anfragen ohne Browser-Header, Cyphers Werkzeuge, Sprache und Injection. Zwei Einschränkungen stehen unten unter Grenzen 1 und 2. „Skript ohne Browser“ im Test heisst „Anfrage ohne die Header eines Browsers“: Ein lokales Programm, das die Header selbst setzt, kommt durch.

### Grenzen (ehrlich)

1. *(Erledigt, siehe Nachtrag.)* **Rote Linie und fremde Programme.** Cyphers eigene Adresse wird bei PowerShell-Web-Befehlen (`Invoke-RestMethod`, `Invoke-WebRequest`) und bei `open_app` als rote Linie erkannt, bei anderen Programmen nicht: `curl.exe -X PUT http://127.0.0.1:8000/api/security/critical-confirm …` ist nur `critical` (`unknown_command`). Das wurde mit dem Klassifizierer geprüft, kein Befehl ausgeführt. Bei eingeschalteter Abfrage fragt das trotzdem. Bei **ausgeschalteter** Abfrage und ohne fremden Inhalt im Turn liefe es ohne Nachfrage. `/api/auth/session` gibt einem lokalen Aufrufer ohne Browser-Header den Token, und `Sec-Fetch-Site` kann ein lokales Programm selbst setzen. So könnte das Modell den Schalter z. B. von „aus (Sitzung)“ auf „aus (dauerhaft)“ stellen. Empfehlung: Loopback-Adressen in jedem Befehlstext als `cypher_secrets` (rote Linie) werten, eine Regel mit Test.
2. **„Nur die lokale Oberfläche“** schützt gegen Handy und Proxy, andere Webseiten, DNS-Rebinding, Sprache und Cyphers Werkzeuge (mit der Lücke aus 1.). Nicht geschützt ist gegen ein anderes Programm, das unter deinem Windows-Konto läuft. Es kann die Header setzen und könnte ebenso direkt die Datenbank ändern. Das kann ein Prozess ohne Adminrechte gegen den eigenen Benutzer nicht verhindern.
3. *(Erledigt, siehe Nachtrag.)* **„Ja“ bei fremdem Inhalt.** Kommt ein kritischer Auftrag (auch einer der roten Linie) aus einem Turn mit fremdem Inhalt, heisst die Karte `untrusted`, und per Sprache reicht dann ein einfaches „Ja“ statt „Ja, ausführen“. Gefragt wird trotzdem, und die Karte nennt den fremden Inhalt. Nicht getestet. Empfehlung: Die Phrase an die Stufe `critical` binden statt an die Kartenart, eine Zeile mit Test.
4. **Klassifizierer:** regelbasiert. Er erkennt so viel Verschleierung, wie die 101 Testbefehle abdecken. Was er nicht versteht, wird `critical`. Das ist sicher, fragt aber auch bei harmlosen, ungewöhnlichen Befehlen.
5. **App-Begriffe:** deterministisch nur für kurze „öffne X“-Sätze. In längeren Sätzen wählt weiter das Modell den Namen (die Beschreibung bittet um das Wort des Nutzers). Das Werkzeug rät dann nicht, aber das Modell kann einen anderen, existierenden App-Namen übergeben. Die eingebauten Begriffe sind eine feste Liste, erweiterbar über `APP_ALIASES`.
6. **Startmenü-Index:** Nach 10 Minuten wird er beim nächsten Gebrauch synchron neu gelesen (laut Modul etwa 1 s, nicht gemessen). Dieser Turn kann dadurch spürbar langsamer sein.
7. **Tastatur/Maus** über `SendInput` ist fragil (Fokus, Fensterlage). Fenster werden über Titel oder Programmname gefunden, mehrdeutig führt zu einer Rückfrage.
8. *(Erledigt, siehe Nachtrag.)* **Testumgebung:** Ein früherer Testlauf als Administrator hat in `%LOCALAPPDATA%\Temp\pytest-of-Silvan` die Einträge `pytest-current` und `pytest-123` mit Administrator-Besitz hinterlassen. Ein normaler `pytest`-Lauf meldet danach am Ende einen `PermissionError` beim Aufräumen, obwohl alle Tests grün sind. Die Läufe hier nutzen deshalb `--basetemp` in einem eigenen Ordner. Entfernen geht nur in einer Administrator-Shell.

## Abschluss

Stand 20. Oktober 2026, Branch `fix/followup-fonts-pccontrol`.

| Lauf | Ergebnis |
|---|---|
| Backend, alle Tests (`pytest -q --basetemp …`) | **832 grün** (52,7 s). Ohne `--basetemp` laufen alle Tests grün durch, aber pytest endet beim Aufräumen mit Exit-Code 1 (Grenze 8) |
| Vitest | **107 grün** (14 Dateien) |
| Playwright, ganze Suite | **9/9 grün** (smoke, followup, fonts, pwa, safety) |
| `tsc -b`, ESLint, Prettier | sauber. Prettier erst nach `8ad7ab1`: 10 Dateien hatten im Arbeitsverzeichnis CRLF, ein frischer Klon ohne das neue Attribut hätte 71, mit ihm 0 |
| `doctor.bat` | keine Fehler, 1 Hinweis: Die laufende Instanz meldet `degraded`, weil `qwen3.5:9b` gerade nicht im Grafikspeicher war (kein Defekt) |

**Offene Punkte**

1. Rote Linie auch für Cyphers Adresse in fremden Programmen (Grenze 1). Empfehlung: Loopback-URLs in jedem Befehlstext als `cypher_secrets` werten, mit Test.
2. „Ja, ausführen“ auch bei kritischen Aufträgen aus fremdem Inhalt verlangen (Grenze 3), mit Test.
3. Checkliste PC-Steuerung in [ACCEPTANCE.md](../ACCEPTANCE.md#pc-steuerung-und-sicherheitsabfrage-fix2-vom-nutzer-durchgeführt) vom Nutzer ausfüllen, vor allem Nr. 14 („Editor“ mit `qwen3.5:4b`).
4. pytest-Reste vom Administrator-Lauf. Option A (empfohlen): einmal in einer Administrator-PowerShell `pytest-current` und `pytest-123` unter `%LOCALAPPDATA%\Temp\pytest-of-Silvan` entfernen, danach geht `pytest -q` wieder ohne Zusatz. Option B: `--basetemp` fest in `pytest.ini` eintragen. Das umgeht das Problem dauerhaft, ändert aber jeden Testlauf.
5. Grafikspeicher und Latenz mit `num_ctx` 12288 messen, dazu die Befehls-Suite mit 36 Werkzeugen. Die Ergebnisse in neue Dateien schreiben, die M5-Baselines nicht überschreiben.

## Nachtrag: Folgearbeiten Sicherheit (Branch `fix/security-followup`)

| Punkt | Commit | Ergebnis |
|---|---|---|
| Rote Linie für diesen PC in jedem Befehl (Grenze 1, offener Punkt 1) | `65a17e3` | `app/security/this_pc.py`: Loopback in jeder Schreibweise, Backend-Adresse samt Port, Tailscale-Name/-IP, egal welches Programm. Auch Variablen, Verkettungen, Umleitungen, URL-Kodierung und Base64 werden erkannt, soweit die festen Regeln reichen. +53 Tests, Policy-Test mit ausgeschalteter Abfrage |
| „Ja, ausführen“ nach Stufe `critical` (Grenze 3, offener Punkt 2) | `7a6a369` | `policy.needs_voice_phrase`: Karten durch Klassifizierer, rote Linie und fremden Inhalt verlangen die Phrase, nicht-kritische behalten „Ja“. +9 Tests, Vitest +1 |
| pytest-Reste vom Admin-Lauf (Grenze 8, offener Punkt 4) | Doku-Commit dieses Nachtrags | `pytest.ini`: `--basetemp=.pytest_tmp`. Vier Tests nahmen an, dass `tmp_path` ausserhalb des Projekts liegt. Sie nutzen jetzt `outside_tmp` im System-Temp. `pytest -q` endet trotz der Reste mit 0 |

Die Grenze „Schadsoftware unter dem eigenen Konto“ (Grenze 2) bleibt bestehen und steht jetzt im README und in ARCHITECTURE.
