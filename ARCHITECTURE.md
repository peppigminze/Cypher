# Cypher – Architektur

Ein Überblick über das Gesamtsystem, für alle, die den Code verstehen
wollen, ohne erst jede Datei einzeln zu lesen. Für Installation und
Bedienung siehe [README.md](README.md).

## 1. Die drei Prozesse

Cypher besteht aus drei unabhängigen Prozessen, die in V1 alle auf
demselben Windows-PC laufen:

```
                    📱 Handy/PWA
                             │ HTTPS (USER_TOKEN)
                             ▼
                    ┌──────────────────┐
                    │  Backend (FastAPI)│   Port 8000
                    │  Messages/Tasks/  │   SQLite (backend/jarvis.db)
                    │  Memory/Sync-Queue│   Reminder-Scheduler (asyncio-Task)
                    └────────┬─────────┘
                             │ HTTPS (AGENT_TOKEN)
                             ▼
                    ┌──────────────────┐
                    │  PC-Agent-Prozess │   agent/run_agent.py
                    │  Agent Core       │   Poll-Loop, alle 5s
                    │  Tool-System      │
                    └────────┬─────────┘
                             │ HTTP (kein API-Key)
                             ▼
                    ┌──────────────────┐
                    │  Lokales LLM      │   Ollama, Port 11434
                    │  (Ollama)         │
                    └──────────────────┘
```

1. **Backend** (`backend/app/`) - FastAPI, hält die SQLite-Datenbank,
   nimmt Nachrichten/Tasks/Memory-Anfragen entgegen, läuft den
   Reminder-Scheduler. Verarbeitet **nie selbst** eine Chat-Nachricht -
   das ist bewusst Aufgabe des Agents (siehe unten, "Warum getrennt?").
2. **Agent** (`agent/`) - separater Python-Prozess. Pollt das Backend
   nach neuen Nachrichten/bestätigten Aktionen, führt die eigentliche
   Cypher-Pipeline aus (LLM fragen, Tools ausführen), meldet das
   Ergebnis zurück.
3. **Frontend** (`frontend/`) - React/TypeScript PWA, spricht nur mit
   dem Backend, nie direkt mit dem Agent oder dem LLM.

Backend und Agent importieren zwar dieselben Python-Module
(`backend/app/...`) und teilen sich dieselbe SQLite-Datei, kommunizieren
aber ausschließlich über die HTTP-Sync-Endpunkte (`/api/sync/*`,
`/api/actions`) miteinander - nicht durch direkten Import von
Laufzeitzustand. Das ist der Haken, der es erlauben würde, den Agent
später auf einem anderen Rechner laufen zu lassen (z. B. wenn das
Backend einmal auf einem Dauerbetrieb-Server läuft) ohne den
Nachrichtenfluss selbst umzubauen.

### Warum getrennte Prozesse?

Die PWA blockiert **nie** auf eine LLM-Antwort. Eine Nachricht wird vom
Backend sofort als `pending` gespeichert und die HTTP-Antwort kommt
augenblicklich zurück - auch wenn der PC gerade aus ist. Der Agent holt
sich `pending`-Nachrichten ab, sobald er (und damit der PC) wieder
online ist. Das ist die Grundlage für "auch nutzbar wenn der PC aus
ist" (README Abschnitt 10).

## 2. Nachrichtenfluss (einfacher Fall)

```
Nutzer tippt in der PWA
    │
    ▼
POST /api/messages (USER_TOKEN)
    │  Backend speichert Message(status=pending), antwortet sofort
    ▼
Agent pollt GET /api/sync/pending (AGENT_TOKEN)
    │  Backend setzt status=processing (verhindert Doppelverarbeitung)
    ▼
CypherAgent.run_pipeline() im Agent-Prozess:
    understand()  - relevante Memories suchen
    plan()        - LLM fragen: welches Tool (falls nötig)?
    execute_tools() - SAFE-Tools laufen sofort
    generate_response() - LLM fasst das Ergebnis in Worten zusammen
    │
    ▼
POST /api/sync/complete (AGENT_TOKEN)
    │  Backend setzt status=completed, speichert response
    ▼
PWA zeigt die Antwort beim nächsten Poll (alle 3s)
```

## 3. Mehrschritt-Pipeline + Bestätigungs-Flow

`CypherAgent.run_pipeline()` (`backend/app/agent/core.py`) ist eine
Schleife, kein einzelner Request/Response-Schritt:

```
plan() → Tool? ──nein──→ done, Antwort formulieren
    │
   ja
    │
    ▼
Tool bekannt? ──nein──→ Beobachtung "unbekanntes Tool", nochmal plan()
    │
   ja
    │
    ▼
schon identisch versucht? ──ja──→ abbrechen, mit vorhandenen
    │                              Beobachtungen zusammenfassen
   nein                            (verhindert Wiederholungs-Loops,
    │                               siehe unten)
    ▼
SAFE? ──ja──→ ausführen, Beobachtung sammeln → nochmal plan()
    │
CONFIRM_REQUIRED
    │
    ▼
Pipeline pausiert: PendingAction anlegen, Message bleibt "processing"
    │
    ▼
Mensch bestätigt/lehnt ab in der PWA (POST /api/actions/{id}/confirm|reject)
    │
    ▼
Agent holt bestätigte Aktion (GET /api/sync/confirmed-actions),
führt sie aus, setzt run_pipeline() mit der neuen Beobachtung fort
(ggf. erneut pausierend, falls ein zweites bestätigungspflichtiges
Tool nötig ist)
```

Begrenzt auf `MAX_AGENT_STEPS` (5) pro Nachricht, damit ein
unkooperatives lokales Modell nicht endlos weiterplant.

**Anti-Duplikat-Schutz:** Kleine lokale Modelle (getestet mit
`llama3.1:8b`) ignorieren gelegentlich die Anweisung, nach einem
erfolgreichen Tool-Aufruf aufzuhören, und wiederholen denselben Aufruf.
Ohne Gegenmaßnahme hätte das reale Duplikate erzeugt (z. B. denselben
Task fünfmal) oder - bei einem fehlgeschlagenen, bestätigungspflichtigen
Tool - eine Endlosschleife aus identischen Bestätigungsanfragen. Die
Pipeline erkennt einen exakt wiederholten `(tool, argumente)`-Aufruf
(egal ob er vorher erfolgreich war oder fehlschlug) und bricht
stattdessen ab. Beide Fälle wurden live gegen den echten Ollama-Prozess
reproduziert und der Fix verifiziert (siehe Commit-Historie).

## 4. Tool-Sicherheitsmodell

Siehe [README.md Abschnitt 16](README.md#16-tool-sicherheit) für die
vollständige Tabelle. Kurzfassung: `SAFE` läuft automatisch,
`CONFIRM_REQUIRED` pausiert immer für eine menschliche Bestätigung.
Datei-/Terminal-Tools sind zusätzlich auf `ALLOWED_DIRECTORIES`
beschränkt (`app/tools/paths.py`, löst Symlinks/`..` auf, bevor
geprüft wird), und `run_command`/`open_application` laufen zusätzlich
gegen einen harten Denylist offensichtlich katastrophaler Muster
(`app/tools/command_safety.py`) - unabhängig von der
Bestätigungsstufe.

## 5. Datenmodell (SQLite, `backend/jarvis.db`)

| Tabelle | Zweck |
|---|---|
| `messages` | Chat-Verlauf, Status-Maschine `pending → processing → completed/failed`, plus `retry_count`/`next_retry_at` für den Retry-Mechanismus |
| `tasks` | Todos + Reminder (kein separates Reminder-Modell - ein Task mit `due_at` + `reminder_enabled` **ist** ein Reminder) |
| `memory_entries` | Langzeit-Gedächtnis, typisiert (`fact`/`preference`/`project`) |
| `pending_actions` | Bestätigungs-Warteschlange für `CONFIRM_REQUIRED`-Tools |
| `agent_heartbeats` | Ein Eintrag pro Agent, für den Online/Offline-Status |
| `command_logs` | Audit-Trail jedes tatsächlich ausgeführten Terminal-Befehls |

Kein Alembic - stattdessen ein minimaler, selbstgebauter
Additiv-Migrationsmechanismus (`ensure_columns()` in
`app/database/db.py`): fügt fehlende Spalten per `ALTER TABLE` hinzu,
verändert oder löscht nie bestehende Daten. Wurde mehrfach live gegen
die echte, bereits befüllte Datenbank verifiziert (siehe
Commit-Historie zu Memory-Typen, Task-Remindern, Message-Retries).

## 6. LLM-Abstraktion

```
LLMProvider (abstract)
├── LocalLLMProvider   - Ollama-kompatibel, Standard, kostenlos
└── CloudLLMProvider   - optional, standardmäßig AUS, nur als Fallback
```

`CypherAgent._chat()` versucht immer zuerst `LocalLLMProvider`. Nur
wenn die einen `LLMUnavailableError` wirft **und** ein
`CloudLLMProvider` konfiguriert ist (`CLOUD_LLM_ENABLED=true` +
API-Key), wird dieser als Fallback versucht. Schlägt auch das fehl (oder
ist kein Cloud-Fallback konfiguriert), greift der Retry-Mechanismus
(Abschnitt 7) statt eines sofortigen permanenten Fehlers.

## 7. Zuverlässigkeit

- **Nachrichten-Retries:** Ein `LLMUnavailableError` (Ollama nicht
  erreichbar) lässt eine Nachricht nicht sofort scheitern, sondern
  requeued sie mit wachsendem Backoff, bis zu `MESSAGE_MAX_RETRIES`
  mal. Live verifiziert: Ollama gestoppt, Retry-Zähler stieg korrekt,
  nach Ablauf aller Versuche sauberer Fehlertext.
- **Reminder-Scheduler ist zustandsbasiert, nicht zeitgesteuert:** Er
  prüft bei jedem Tick "ist `due_at` vorbei und wurde für dieses
  Vorkommen noch nicht benachrichtigt?" - kein In-Memory-Timer. Das
  bedeutet: War der PC/Backend beim Fälligkeitszeitpunkt aus, feuert
  der Reminder beim nächsten Tick nach dem Neustart trotzdem korrekt,
  statt verloren zu gehen.
- **Naive/Aware-Datetime-Falle:** SQLite speichert keine echte
  Zeitzoneninformation - ein mit `datetime.now(timezone.utc)`
  geschriebener Wert kommt beim Zurücklesen als naiver Wert zurück.
  Trat live als echter Bug im Reminder-Scheduler auf (`TypeError:
  can't compare offset-naive and offset-aware datetimes`) und wurde
  behoben (`_as_utc()`-Normalisierung); an mehreren Stellen im Code
  wiederkehrendes Muster, siehe `api/agent.py::system_status` für ein
  weiteres Beispiel.

## 8. Warum bestimmte Dinge fehlen

Diese Lücken sind bewusste Architekturgrenzen von V1, kein Zufall:

- **Kein PWA-"PC aufwecken"-Button:** Backend und Agent laufen auf dem
  PC, der geweckt werden soll - sind unmöglich in der Lage, sich selbst
  ein Wake-on-LAN-Paket zu schicken, wenn sie aus sind. Ein
  Browser/eine PWA kann außerdem grundsätzlich kein rohes UDP-Paket
  senden. Siehe [README Abschnitt 18](README.md#18-wake-on-lan).
- **Cloud-Fallback nur mit lokalem LLM als primärer Quelle:** Da
  Backend und Agent co-lokiert sind, ist das Szenario "PC komplett aus,
  Handy erreicht trotzdem einen Cloud-Fallback über einen extern
  gehosteten Server" in V1 nicht erreichbar - dafür müsste das Backend
  auf einem separaten, dauerhaft laufenden Host liegen. Was heute
  funktioniert: der Agent-Prozess läuft, aber Ollama selbst antwortet
  nicht.

## 9. Repository-Layout

Siehe [README.md Abschnitt 13](README.md#13-projektstruktur) für die
vollständige Verzeichnisstruktur inklusive `scripts/` (Wake-on-LAN,
Autostart - beide bewusst eigenständige, abhängigkeitsarme Skripte
außerhalb von `backend`/`agent`, da sie auf einem anderen Rechner
bzw. ganz ohne laufenden Cypher-Prozess funktionieren müssen).
