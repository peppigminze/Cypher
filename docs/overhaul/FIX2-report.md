# FIX2 – Folge-Zuhörfenster, lokale Schriften, volle PC-Steuerung

Branch `fix/followup-fonts-pccontrol`, drei Punkte, je ein Commit.

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
