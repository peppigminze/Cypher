# Cypher – Projektplan

Stand: 8. Oktober 2026, nach der Komplettüberholung (Branch `overhaul/v1`, Meilensteine M0–M8).

## Ziel

Ein persönlicher Sprachassistent im Stil von JARVIS, der **lokal** auf dem eigenen Windows-PC läuft:
- schnell genug für ein echtes Gespräch (unter 2 Sekunden bis zur ersten Silbe),
- steuert den PC sicher (Freigabe für alles Riskante, Audit-Log),
- merkt sich, was man ihm sagt, und lässt sich dabei zuschauen und korrigieren,
- funktioniert auch vom Handy aus,
- kostet nichts im Betrieb (Cloud nur optional und nur mit ausdrücklicher Zustimmung).

## Stand

| Meilenstein | Inhalt | Ergebnis |
|---|---|---|
| M0 Audit & Plan | Ist-Zustand gemessen, Risiken, Plan | 2,5 s Latenz mit Tippen fürs Aufnahmeende, zwei Modelle im Grafikspeicher (10–20× langsamer), Token im Frontend-Bundle → [AUDIT.md](docs/overhaul/AUDIT.md), [PLAN.md](docs/overhaul/PLAN.md) |
| M1 Fundament | Konfiguration mit Prüfung, Logging, Fehler mit deutschen Hinweisen, Health, Modell-Manager | Ein-Modell-Politik, Warm-up, Erholung ohne Neustart |
| M2 Sicherheit & Tools | Token-Handling, Origin-Prüfung, Rate-Limits, Tool-System mit Risikostufen, Audit, Prompt-Injection-Schutz, echte Websuche/Wetter/Lautstärke/Medien | 7 Injection-Tests, live 0 versuchte Aktionen |
| M3 Sprache | WebSocket-Pipeline, VAD, vorsorgliche Erkennung, Aktivierungswort, Unterbrechen, Echo-Schutz | 1,24 s bis zur ersten Silbe (vorher 2,5 s + Tippen) |
| M4 Gedächtnis | Kern (JSON) + Archiv (FTS5), Merken/Vergessen, Gesprächsverlauf über Neustarts | sichtbar und editierbar im Menü |
| M5 LLM & Persona | Modellentscheid per Messung, Wächter gegen „behauptet, aber nicht getan“, Cloud über die Responses API mit Kostenzähler, alter Agent entfernt | qwen3.5:9b: 0 falsche Argumente, 0 Halluzinationen in der Befehls-Suite |
| M6 Oberfläche | eine Sprachansicht im JARVIS-Stil, verstecktes Menü, gestaltete Fehlerzustände, ESLint/Prettier, Playwright | 81 Vitest + 5 Playwright |
| M7 Betrieb | setup/start/stop/doctor/backup per Doppelklick, Oberfläche vom Backend, Handy über Tailscale mit QR-Kopplung | frische Installation getestet |
| M8 Abschluss | Doku, automatischer Abnahmetest, Feinschliff aus dessen Funden | Definition of Done 11/11, drei Läufe in Folge |

Details pro Meilenstein: [docs/overhaul/](docs/overhaul/). Abschluss: [FINAL.md](docs/overhaul/FINAL.md). Abnahme: [ACCEPTANCE.md](docs/ACCEPTANCE.md).

## Nächste Schritte (Vorschläge, nach Nutzen sortiert)

1. **Manuelle Abnahme** mit echter Stimme, Doppelklick-Start und Autostart ([ACCEPTANCE.md](docs/ACCEPTANCE.md#manuelle-checkliste-braucht-dich)).
2. **Handy einrichten** nach [MOBILE.md](docs/MOBILE.md) und die Prüfliste dort abhaken.
3. **Alltagsdaten sammeln:** Fehler im Audit-Log und in Diagnose → Latenzen beobachten. Neue Problemsätze als Fälle in `backend/eval/commands_de.json` aufnehmen und mit `python -m eval.command_suite` messen, bevor man am Prompt dreht.
4. **„Das ist zu laut“ direkter:** Das Modell fragt dort manchmal zurück, statt leiser zu machen. Ein Kurzbefehl für eindeutige Formulierungen wäre der einfachste Weg.
5. **Aktivierungswort mit trainiertem Modell**, falls Fehl-Wecken oder Aussetzer im Alltag stören. Die Schnittstelle (`WakeWordDetector`) ist dafür vorbereitet. Voraussetzung: ein Modell mit permissiver Lizenz.
6. **Alte Datenbanktabellen** (`messages`, `pending_actions`, `agent_heartbeats`, `command_logs`) mit einer Migration archivieren, sobald sicher ist, dass niemand sie mehr braucht.

## Bewusst nicht im Umfang

Aus dem Auftrag, unverändert:
- Discord- oder app-übergreifende Steuerung
- Smart-Home
- Kalender- und Mail-Integration
- Training eigener Modelle
- Firebase/Cloud-Hosting

Ausserdem bewusst nicht gemacht (Begründung in den Reports):
- Whisper auf der GPU (Grafikspeicher gehört dem Sprachmodell, M3)
- Streaming-Spracherkennung (Whisper arbeitet in 30-s-Fenstern, M3)
- Vektor-Datenbank fürs Gedächtnis (FTS5 reicht, keine neue Abhängigkeit, M4)
- Ein „PC aufwecken“-Knopf in der App (ein Browser kann kein Magic Packet senden; dafür `scripts/wake_pc.py` auf einem zweiten Gerät)

## Leitplanken

- Nur permissive Lizenzen (MIT/Apache/BSD), jede Abhängigkeit in [DEPENDENCIES.md](docs/DEPENDENCIES.md).
- Keine Secrets im Repo: `.env` bleibt gitignored, `.env.example` wird gepflegt.
- Keine Breaking Changes ohne Migration: alte `.env`-Dateien starten weiter, alte Daten bleiben.
- Modellnamen, Bibliotheks-APIs und Cloud-Parameter immer gegen die aktuelle Doku prüfen, nie aus dem Gedächtnis.
- Nichts als „funktioniert“ melden, was nicht ausgeführt wurde.
