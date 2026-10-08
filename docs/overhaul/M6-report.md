# M6-Report – Frontend

**Status: abgeschlossen.** Die Oberfläche hat nur noch einen Bildschirm, die Sprachansicht. Alles andere liegt in einem versteckten Systemmenü (Menü-Knopf oder Taste **M**).

Ein Frontend-Design-Skill war in dieser Umgebung nicht installiert; Gestaltung und Umsetzung sind eigene Arbeit nach den Vorgaben unten.

## Was gebaut wurde

| Anforderung | Umsetzung |
|---|---|
| JARVIS-Ästhetik | Ruhiges Instrumentenpanel im Dunkeln. Eine Signalfarbe (Cyan), Violett = denkt, Amber = braucht dich, Rot = kaputt. HUD-Leiste mit Telemetrie in Mono-Schrift, feines Raster mit Vignette (`src/styles/hud.css`). |
| Bubble mit 60 fps, audio-reaktiv | `CoreVisualizer.tsx`: Canvas mit einem Zeichenaufruf pro Bildschirm-Frame, ohne React-State pro Frame. 96 Ticks folgen dem **Frequenzspektrum**: dem Mikrofon beim Zuhören, Cyphers Stimme beim Sprechen (je ein `AnalyserNode`). Dazu ein „Energiekern“, der mit der Lautstärke wächst, und ein segmentierter Innenring. Beim Denken kreisen Bögen, bei einer Rückfrage pulsiert ein amberfarbener Ring. |
| reduced-motion | Mit `prefers-reduced-motion` wird ein stehendes Bild pro Zustand gezeichnet, ohne Animation. CSS-Übergänge sind dann aus. |
| Live-Untertitel | Deine Worte (blass) und Cyphers Antwort (hell, Markdown bereinigt). Laufende Tools („Ich schaue nach …“) erscheinen ebenfalls. |
| Status Weckwort/VAD | HUD zeigt Modus, Mikrofon und letzte Latenz. Die Statuszeile sagt „Sag „Hey Cypher“ …“, „Ich höre zu …“ bzw. „Ich höre …“, sobald die Pausenerkennung Sprache hört. |
| Freigabekarte per Klick, Taste, Stimme | Gemeinsame `ApprovalCard` für Sprache und Chat: Risiko-Etikett, genaue Beschreibung, Ablauf-Countdown. Bedienung über Knöpfe, **J/N**, **Esc** = nein, oder gesprochenes „ja“/„nein“. Die Cloud-Rückfrage hat ein eigenes violettes Etikett „Verlässt deinen PC“. |
| Verstecktes Menü | `SystemMenu`: Chat, Gedächtnis, Aufgaben, Projekte, **Diagnose** (Komponenten, Latenzen, Kontextfenster, Konfiguration), **Audit-Log** (Filter: Freigaben / Verweigert / Fehlgeschlagen), **Logs** (Level-Filter), Einstellungen. Modaler Dialog mit Fokusfalle, Esc schliesst, Fokus geht zurück. |
| Mobil / PWA | Auf schmalen Bildschirmen wird die Menü-Navigation zur Reiterleiste. Safe-Area-Ränder für iPhone sind berücksichtigt. Kein seitliches Scrollen bei 375 px (Playwright-Test). |
| Gestaltete Fehlerzustände | `useHealth` fragt `/api/health` ab (öffentlich, funktioniert auch vor dem Koppeln): alle 10 s, bei Problemen alle 3 s. `HealthBanner` sagt, **was** fehlt, **was das heisst** und **was zu tun ist** („Sprachmodell-Server – Ollama läuft nicht. Starte Ollama …“, „Cypher-Backend nicht erreichbar – starte `start_cypher.bat`“). Das Banner verschwindet von selbst, sobald es wieder geht. Dazu kommen die Fehler eines Turns (deutsch, mit Hinweis), ein gesperrtes Mikrofon (mit Weg zur Browser-Einstellung) und HTTP ohne HTTPS auf dem Handy. |
| Barrierefreiheit | Alle Bedienelemente haben Namen. Statuszeile, Untertitel und Banner sind Live-Regionen. Die Freigabe ist ein `alertdialog`. Sichtbare Fokusrahmen, vollständige Tastaturbedienung (Leertaste, Esc, J, N, M), Kontrast auf dunklem Grund ausreichend. Eine automatische axe-Prüfung gibt es nicht (keine weitere Abhängigkeit), siehe Checkliste. |
| Formale Zustandsmaschine | `src/hooks/voiceMachine.ts`: explizite Übergangstabelle statt XState. Sie enthält (1) den erwarteten Ablauf des Servers (`SERVER_FLOW`, unerwartete Wechsel warnen im Dev-Modus) und (2) die Tabelle „Eingabe × Zustand → Befehl“ für Tippen, Leertaste, Esc und J/N. Tests prüfen, dass jeder Zustand jede Eingabe beantwortet, ein versehentliches Tippen keine Freigabe entscheidet und das Mikrofon nur sendet, wenn der Server zuhört. |
| WS/SSE-Streaming mit Reconnect, AudioWorklet | Seit M3/M5 (Sprache über WebSocket mit Backoff, Chat über SSE). |
| `.env.local`-Warnung, TS strict | Seit M2 bzw. vorhanden. |
| ESLint / Prettier | Flat-Config mit TypeScript- und React-Hooks-Regeln. Die neuen React-Compiler-Regeln haben 3 echte Fehler gefunden: eine Ref-Zuweisung während des Renderns und zweimal synchrones `setState` im Effekt. Alle behoben. Der Code ist einmalig formatiert, die Werkzeuge sind auf Versionen festgelegt, die mindestens zwei Wochen alt sind. |
| Vitest + Playwright | Siehe Tests. Playwright läuft im **vorhandenen Edge** (`channel: msedge`), es wird kein Browser heruntergeladen. Backend und WebSocket sind nachgebildet: Der Test braucht weder Ollama noch Mikrofon und erreicht nie dein echtes Backend. |
| PWA ohne API-Caching | Der Service Worker speichert nur die App-Hülle. `navigateFallbackDenylist` für `/api/`, `/health`, `/speak`, `/transcribe`, **keine** Laufzeit-Caches. Ein Test prüft das gebaute `sw.js`. |

Entfernt wurden Sidebar, StatusBadge und die alte CSS-Bubble. Aus `App.css` sind dabei 1009 Zeilen alter Stile weggefallen.

## Tests

- **Vitest: 81 grün.** Neu:
  - `voiceMachine.test.ts` (5)
  - `system.test.tsx` (7): Fehler-Banner und Reihenfolge, Menü-Dialog mit Fokus und Esc, Audit-Filter
- **Playwright (Edge): 5 grün.**
  1. Sprachansicht laden, verbinden, getippten Befehl beantworten. Die Untertitel und „0,8 S“ stehen im HUD.
  2. Ollama aus → gestaltetes Fehlerbanner.
  3. Menü mit **M**, Diagnose mit Latenz und Kontextfenster, **Esc** schliesst.
  4. Handy-Breite 375 px ohne seitliches Scrollen.
  5. Service Worker ohne API-Cache.
- `npm run lint`, `npx tsc -b` und `npm run format:check` sind sauber.

## Smoke-Test (echtes Backend, eingebauter Browser)

- **Sprachansicht:** Der Kern pulsiert ruhig, darunter steht „Tippen zum Sprechen“.
- **Getippte Frage** („Erklär mir in zwei Sätzen, was ein Schwarzes Loch ist.“):
  - Der Kern wechselt auf „Sprechen“, und die Ticks schlagen sichtbar mit Cyphers Stimme aus.
  - Die Antwort läuft als Untertitel mit, das HUD zeigt „0,8 S“.
- **Menü:** Die Diagnose zeigt alle Komponenten grün mit deutschen Meldungen (inklusive „AMD Radeon RX 9070 XT: Modell komplett im Grafikspeicher“). Das Audit-Log zeigt die Einträge, Desktop- und Handy-Layout getestet.

## Manuelle Checkliste (braucht dich)

- [ ] Mit echtem Mikrofon „Tippen“ und sprechen: Die Ticks folgen deiner Stimme, beim Antworten der Stimme von Cypher.
- [ ] Windows-Einstellung „Animationen anzeigen“ aus (bzw. reduced motion): Der Kern steht still, die Bedienung bleibt gleich.
- [ ] Nur mit Tastatur: Tab durch die Bedienung, Leertaste spricht, M öffnet das Menü, Esc schliesst es.
- [ ] Ollama beenden: Innerhalb weniger Sekunden erscheint das rote Banner. Ollama starten: Das Banner verschwindet ohne Neuladen.
- [ ] Handy (nach M7 über Tailscale-HTTPS): App „zum Home-Bildschirm hinzufügen“, öffnen, sprechen.
- [ ] Mit einem Screenreader (Windows: Sprachausgabe, Strg+Win+Enter) einmal einen Befehl tippen. Die Antwort wird vorgelesen (Live-Region).

## Offene Punkte

- In `frontend/.env` steht noch das veraltete `VITE_USER_TOKEN` (Build warnt, wird nicht benutzt). Kannst du löschen.
- Im Audit-Log stehen einige „Freigabe gefragt“-Einträge aus meinen Befehlsreihen-Läufen vor dem Fix in M6. Die Suite schreibt seither nicht mehr ins Audit-Log. Ich habe die Einträge nicht gelöscht, weil das Audit-Log nie automatisch bereinigt wird.
- Das Ausliefern des gebauten Frontends durch das Backend (eine Adresse für alles) folgt in M7.
