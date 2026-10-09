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
