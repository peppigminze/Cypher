# Abschlussbericht – Komplettüberholung Cypher

**Status: abgeschlossen.** Alle Meilensteine M0–M8 sind auf `overhaul/v1` committet. Die Definition of Done ist erfüllt: **11/11 im automatischen Abnahmetest, in drei Läufen hintereinander** ([ACCEPTANCE.md](../ACCEPTANCE.md)). Offen sind nur Punkte, die echte Hardware, dein Konto oder deine Bestätigung brauchen (Checkliste unten).

## Vorher → Nachher

| | Vorher (Audit, M0) | Nachher |
|---|---|---|
| Vom Sprechende bis zur ersten Silbe | 2,52 s, und das Sprechende musste man per Tippen anzeigen | **1,24–1,87 s** automatisch erkannt. „Hey Cypher, wie spät ist es?“: ~2,0 s. Mit Pause nach „Hey Cypher“: 1,3–1,6 s. |
| Nach Leerlauf | 11,9 s (Modell entladen nach 5 min) | Warm-up beim Start, Keep-Alive 30 min, Erholung ohne Neustart |
| Grafikspeicher | bis 3 Modelle gleichzeitig, Auslagerung → 10–20× langsamer, ohne Meldung | ein Modell, 100 % GPU, Doktor und Health melden Auslagerung |
| Bedienung | Push-to-Talk, kein Unterbrechen | Aktivierungswort, automatisches Sprechende, Unterbrechen per Tippen (2 ms) oder Stimme (0,6–0,9 s) |
| Ausführung | zwei Prozesse, Agent holt alle 5 s Aufträge ab | ein Prozess, Tools direkt, Antwort gestreamt Satz für Satz |
| „Leiser“, Uhrzeit | kein Lautstärke-Tool, Uhrzeit in UTC | Kurzbefehle ohne Sprachmodell (~0,3 s), richtige Zeitzone |
| Behauptete Aktionen | „Erledigt.“ auch nach einem Nein | Wächter hält falsche Erfolgsmeldungen zurück: 0 Halluzinationen in der Befehls-Suite |
| Sicherheit | Token im JS-Bundle, keine Origin-Prüfung, kein Rate-Limit, SSRF-Lücke, Prompt-Injection ungeschützt, kein Audit | Token nur auf dem PC oder per Kopplung, Origin/Rebinding/Proxy-Prüfung, Rate-Limits, SSRF-Schutz, Untrusted-Marker + Taint, Audit jeder Entscheidung |
| Health | `/health` immer „ok“ | Ampel pro Komponente mit deutschem Hinweis, Diagnose mit Latenzen und Kontextbudget |
| Gedächtnis | eine Tabelle ohne Suche | Kern + Archiv mit Volltextsuche, übersteht Neustarts, im Menü editierbar |
| Cloud | Platzhalter | Responses API mit offiziellem SDK, nur manuell oder nach „Ja“, Kostenzähler und Monatsbudget |
| Betrieb | Befehle von Hand, alter Autostart | `setup`/`start`/`stop`/`doctor`/`backup` per Doppelklick, Oberfläche vom Backend, Handy per QR |
| Tests | 262 Backend, 61 Frontend | **597** Backend, **85** Vitest, **5** Playwright, Abnahmetest gegen echte Instanz |

## Meilensteine

| | Bericht | Commit |
|---|---|---|
| M0 Audit & Plan | [AUDIT.md](AUDIT.md), [PLAN.md](PLAN.md), [M0-report.md](M0-report.md) | `94ebeeb` |
| M1 Fundament | [M1-report.md](M1-report.md) | `6a5de06` |
| M2 Sicherheit & Tools | [M2-report.md](M2-report.md) | `10361ce` |
| M3 Sprache | [M3-report.md](M3-report.md) | `de005d0`, `337c2d6`, `825a2a8` |
| M4 Gedächtnis | [M4-report.md](M4-report.md) | `5f5ed22` |
| M5 LLM & Persona | [M5-report.md](M5-report.md) | `537a399`, `d2ef8b5`, `67348b0`, `38e0f46` |
| M6 Oberfläche | [M6-report.md](M6-report.md) | `7d374a1`, `8e1c9ac` |
| M7 Betrieb | [M7-report.md](M7-report.md) | `46736b5` |
| M8 Abschluss | dieser Bericht | siehe `git log` |

## M8 im Detail

**Dokumentation:** [README.md](../../README.md) neu (Schnellstart, Bedienung, Sicherheit, Konfiguration, Fehlerbehebung, Mermaid-Übersicht). [ARCHITECTURE.md](../ARCHITECTURE.md) neu (nach `docs/` verschoben): Komponenten- und Sequenzdiagramm, Engine, Sicherheit, Gedächtnis, Entscheidungen mit Messwerten. [cypher-projektplan.md](../../cypher-projektplan.md) neu angelegt (existierte laut M0 nicht). [ACCEPTANCE.md](../ACCEPTANCE.md). `TODO.md` verweist auf die aktuellen offenen Punkte (die alte Liste ist erledigt).

**Automatischer Abnahmetest** (`backend/eval/acceptance.py`): Er startet eine isolierte echte Instanz (eigener Port, eigene Daten, eigener Token). Ein Ausfall von Ollama wird über einen abschaltbaren Proxy simuliert, die Cloud über einen lokalen Nachbau der Responses API. Die Stimme kommt per Piper in Echtzeit über den echten WebSocket. 11 Prüfungen entlang der Definition of Done.

**Was er gefunden hat** (alles behoben, mit Tests):
1. **„Merk dir …“ unzuverlässig:** In 2 von 4 Läufen behauptete das Modell „gemerkt“, ohne das Tool aufzurufen. Der Wächter fing das ab, gespeichert wurde trotzdem nichts. Jetzt nennt der Wiederholungshinweis das eindeutige Tool beim Namen: `remember` 30/30 (vorher 18/20). Nebeneffekt: `forget` und `cancel_timer`, in M5 die schwächsten Fälle, schaffen jetzt je 4/4.
2. **Unterbrechen per Aktivierungswort** schlug manchmal fehl („Halsaifer stop.“, Wertung 0,82 < 0,85). Der phonetische Vergleich kennt jetzt auch die Aussprache „Saifer“, aber nur für die zusammengezogene Form, damit „Seifert kommt morgen“ nicht weckt.
3. **Erstes „Hey Cypher“ nach dem Start** ~1 s langsamer: Das Modell fürs Aktivierungswort wird jetzt beim Start mit aufgewärmt.

**Aufgeräumt:**
- Tote Einstellungen der alten Pipeline (`MESSAGE_MAX_RETRIES`, `MESSAGE_RETRY_BACKOFF_SECONDS`) aus Code, API und `.env.example` entfernt. Eine bestehende `.env` startet weiter.
- Acht ungenutzte Frontend-API-Funktionen samt Typen entfernt.
- Texte, die noch `/api/tool-chat` nannten, aktualisiert.
- Der Doktor gibt JSON jetzt immer als UTF-8 aus.
- Browser-Icon ergänzt: Ohne `<link rel="icon">` fragte jeder Browser `/favicon.ico` ab und protokollierte einen 404. Service Worker in echtem Edge geprüft: registriert und aktiv, keine Konsolenfehler.

**Regression geprüft:** Befehls-Suite (67 Fälle × 2) nach der Hinweis-Änderung: 0 Halluzinationen, Argumente 62/62, kein Tool wo keins nötig 50/50. Die verbleibenden Fehltreffer sind dieselben Fälle wie in M5.

## Abweichungen vom Plan

| Plan (M0) | Umgesetzt | Warum |
|---|---|---|
| Wake-Word mit Whisper `base` | Whisper `tiny` + phonetischer Vergleich | schneller, Treffsicherheit mit dem Vergleich ausreichend (M3) |
| Risikostufe `reversible` | `control` (sofort) + `write` (Freigabe) | „Leiser“ mit Freigabekarte wäre unbenutzbar, umkehrbare Steuerung braucht keine Karte (M2) |
| XState oder eigene Tabelle | eigene, getestete Übergangstabelle | keine neue Abhängigkeit (M6) |
| `setup` setzt `OLLAMA_MAX_LOADED_MODELS` | `start_cypher` setzt es nur für das selbst gestartete Ollama. Dauerhaft als Benutzervariable ist es ein Schritt in der Checkliste | eine dauerhafte Systemänderung mache ich nicht ungefragt |
| Handy über `API_HOST`=Tailscale-IP | `tailscale serve` mit HTTPS, Backend bleibt auf 127.0.0.1 | nur mit HTTPS gibt es Mikrofon auf dem Handy (M7) |

## Was du noch tun musst

Nichts davon blockiert den Betrieb.

- [ ] Die **manuelle Checkliste** in [ACCEPTANCE.md](../ACCEPTANCE.md#manuelle-checkliste-braucht-dich): echte Stimme, Doppelklick-Start, Autostart, optional echte Cloud, Handy, Restore üben.
- [ ] **Autostart umstellen** (die alte Aufgabe „JARVIS Autostart“ läuft noch, startet aber schon die neue Version): `backend\.venv\Scripts\python.exe scripts\autostart.py install` und UAC bestätigen.
- [ ] Optional dauerhaft setzen: Benutzer-Umgebungsvariable `OLLAMA_MAX_LOADED_MODELS=1`, falls Ollama bei dir über das Tray-Symbol startet.

Hinweise zu deinen lokalen Dateien (nicht im Repo, ich habe sie nicht verändert):
- `frontend/.env` enthält noch `VITE_USER_TOKEN`. Die Zeile wird ignoriert und kann weg; sie erzeugt bei jedem Build eine Warnung.
- `.env` hat `CLOUD_LLM_MODEL=gpt-5.6-luna`. Das ist gültig, aber `gpt-6-luna` (neuer Standard) kostet etwa die Hälfte. Ändern, falls du die Cloud nutzt.
- Im Audit-Log stehen Einträge aus Trockenläufen der Befehls-Suite vor M5 (vor dem Fix, der das verhindert). Sie lassen sich nicht einzeln löschen. Ich habe sie bewusst stehen lassen.
- In Ollama liegen neben `qwen3.5:9b` und `qwen3.5:4b` (Standard und Ersatz, beide gebraucht) noch `qwen2.5:7b`, `qwen3.5:2b` und `llama3.1:8b` von früher, zusammen ~12 GB. Cypher nutzt sie nicht mehr. Löschen nur, wenn du sie auch sonst nicht brauchst: `ollama rm <name>`.

## Bekannte Grenzen

Siehe [ACCEPTANCE.md](../ACCEPTANCE.md#bekannte-grenzen): Das Aktivierungswort ist kein trainiertes Modell („Heiße Seife …“ am Satzanfang weckt). „Das ist zu laut“ fragt manchmal zurück. Der Handy-Weg ist nicht live getestet.

**Eine offene Entscheidung für dich:** Die Oberfläche lädt ihre Schriften (Space Grotesk, Inter, Share Tech Mono) von Google Fonts. Beim Öffnen geht also eine Anfrage an Google, ohne Internet fallen die Schriften auf Systemschriften zurück. Lokal einbinden ginge, aber die Schriftdateien stehen unter der SIL Open Font License, und die ist nicht auf der erlaubten Liste (MIT/Apache/BSD). Entweder du lässt OFL für Schriften zu, oder wir bleiben bei Google Fonts bzw. stellen auf Systemschriften um.
