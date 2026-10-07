# Cypher über Tailscale vom Handy erreichen

Phase 8. Macht Cypher (Backend + PWA-Frontend) von deinem Handy aus
erreichbar, ohne das Backend dem offenen Internet oder deinem normalen
WLAN auszusetzen - über [Tailscale](https://tailscale.com), ein
WireGuard-basiertes privates Netzwerk (dein "Tailnet") zwischen deinen
eigenen Geräten.

Betrifft nur den *Zugriffspfad*. Nichts an der Tool-Calling-Pipeline,
dem Approval-Gate (Phase 5.5/5.6) oder dem Cloud-Toggle (Phase 7) ändert
sich - beide funktionieren über Tailscale exakt wie lokal.

## 1. Tailscale auf dem PC installieren

1. [tailscale.com/download/windows](https://tailscale.com/download/windows)
   herunterladen und installieren.
2. Beim ersten Start öffnet sich ein Browser-Tab zum Einloggen (Google-,
   Microsoft- oder GitHub-Konto reicht für den Anfang - ein Tailnet mit
   nur deinen eigenen Geräten wird automatisch angelegt).
3. Tailscale-App auf dem Handy installieren (App Store / Play Store),
   mit demselben Konto einloggen.
4. Prüfen, ob alles läuft:
   ```powershell
   powershell -File scripts\check_tailscale.ps1
   ```
   Das Skript sagt dir, ob Tailscale installiert/aktiv ist und nennt die
   **Tailscale-IPv4-Adresse** deines PCs (Format `100.x.y.z`) - die
   brauchst du gleich für `.env`.

Beide Geräte müssen im selben Tailnet eingeloggt sein - sonst sieht das
Handy den PC schlicht nicht.

## 2. Backend für Tailscale binden

In der `.env` im Projekt-Root (nicht `.env.example`):

```bash
API_HOST=100.x.y.z          # deine Tailscale-IP aus Schritt 1
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://100.x.y.z:5173
USER_TOKEN=<echter-zufalls-token>     # NICHT change-me-user-token!
AGENT_TOKEN=<echter-zufalls-token>    # NICHT change-me-agent-token!
```

**Warum genau diese Tailscale-IP statt `0.0.0.0`:** Bindet der Prozess
an `100.x.y.z`, nimmt das Betriebssystem Verbindungen *ausschließlich*
auf dieser Adresse an - ein Gerät in deinem normalen WLAN, das die
reguläre LAN-IP des PCs anspricht, bekommt schlicht "connection
refused", weil dort niemand lauscht. `0.0.0.0` würde dagegen auf *jeder*
Netzwerkschnittstelle lauschen (Tailscale UND normales WLAN UND, falls
dein Router UPnP macht, im schlimmsten Fall das Internet) - genau das
sollte hier vermieden werden.

**CORS ohne Wildcard:** `CORS_ORIGINS` muss die exakte Origin enthalten,
von der das Frontend tatsächlich ausgeliefert wird (hier:
`http://100.x.y.z:5173`, dieselbe IP wie oben, Port des Vite-Dev-Servers
bzw. deines Hostings). Ein `"*"` würde jeder beliebigen Webseite im
Browser deines Handys erlauben, authentifizierte Requests ans Backend zu
schicken, sobald der Token irgendwo im Browser vorhanden ist - das ist
mit einer festen Origin-Liste nicht möglich.

**Starten:**
```bash
cd backend
python run.py
```
`run.py` ist neu (Phase 8) und liest `API_HOST`/`API_PORT` tatsächlich
aus `.env` - anders als die bisherige, in README.md dokumentierte
`uvicorn app.main:app --host ... --port ...`-Zeile, bei der du die
Bind-Adresse jedes Mal selbst tippst (die funktioniert für lokale
Entwicklung weiterhin unverändert).

## 3. Sicherheits-Trade-off: reicht Tailscale + bestehender Token?

**Ja - ohne einen zusätzlichen Shared Secret obendrauf.** Kurze
Begründung, keine übergestülpte Lösung:

- **Tailscale ist bereits ein starker Netzwerk-Perimeter.** Nur Geräte,
  die in *dein* Tailnet eingeloggt sind (WireGuard-verschlüsselt,
  Tailscale-Konto-Auth), können den PC über diese IP überhaupt
  erreichen - nicht "jeder im selben WLAN", nicht das offene Internet.
- **Jeder Endpoint verlangt bereits einen Bearer-Token**
  (`require_user`/`require_agent`, seit Phase 0, gilt für
  `/api/tool-chat/confirm`, `/api/settings/provider`, `/api/usage`
  genau wie für alles andere) - das ist bereits die "einfache
  Zusatz-Absicherung", nach der Anforderung 2 fragt. Ein zweites,
  separates Secret obendrauf würde denselben Angriffsvektor doppelt
  behandeln: Beide Werte lägen im selben `frontend/.env.local`, würden
  also bei einem Leak (z. B. gestohlenes, entsperrtes Handy) gemeinsam
  kompromittiert - kein echter Sicherheitsgewinn, nur zusätzliche
  Komplexität.
- **Der reale Schwachpunkt war nicht "kein zweiter Token", sondern**
  "der bestehende Token könnte noch der öffentliche `.env.example`-
  Platzhalter sein." Das ist harmlos, solange nur `127.0.0.1` erreichbar
  ist (ein Angreifer bräuchte dafür schon Code-Ausführung auf dem PC) -
  aber real, sobald der Perimeter sich auf dein Tailnet ausweitet (z. B.
  wenn du dein Tailnet je mit jemand anderem teilst). Deshalb die
  konkrete Härtung in diesem Phase: **der Backend-Start verweigert sich
  jetzt hart** (`RuntimeError`, kein Serverstart), wenn `API_HOST`
  außerhalb von `127.0.0.1`/`localhost` liegt UND `USER_TOKEN` oder
  `AGENT_TOKEN` noch der Platzhalter ist (`app/main.py::_check_tokens`).
  Bei reinem `127.0.0.1`-Betrieb bleibt es wie bisher bei einer Warnung,
  um bestehende lokale Setups nicht zu brechen.

**Wichtige Einschränkung:** Diese Prüfung sieht die Wahrheit über die
Bind-Adresse nur, wenn du **`python run.py`** zum Starten benutzt (das
liest `API_HOST` wirklich aus `.env`). Startest du stattdessen die alte
`uvicorn ... --host 0.0.0.0 ...`-Zeile von Hand, wird die Prüfung
umgangen - genau wie `API_HOST`/`API_PORT` davor schon wirkungslos
waren, wenn man sie ignoriert. Für Tailscale-Zugriff also **immer**
`python run.py` verwenden, nicht die alte manuelle Uvicorn-Zeile.

## 3b. Wichtige Einschränkung: Mikrofon braucht HTTPS

**Die Voice-UI (Mikrofonaufnahme, `getUserMedia`) funktioniert über
reines `http://100.x.y.z:5173` NICHT.** Browser verlangen für
Mikrofonzugriff einen "Secure Context" (HTTPS oder `localhost`) - das
ist eine reine URL-Schema-Prüfung des Browsers, die nicht weiß, dass
die Verbindung über Tailscale ohnehin schon WireGuard-verschlüsselt
ist. Ohne weitere Maßnahme bekommst du auf dem Handy also den
"Mikrofon nicht verfügbar"-Hinweis der Voice-UI (`isSupported` schlägt
sauber fehl, kein Crash - aber eben auch kein Mikrofon). Alles andere
(Chat-Text-Eingabe, Settings, Provider-Umschaltung, `/api/tool-chat`
per curl/Text) funktioniert über reines HTTP normal.

**Sauberer Fix (optional, nur nötig wenn du die Sprachfunktion vom
Handy aus willst):** Tailscale kann selbst gültige HTTPS-Zertifikate
für den eigenen MagicDNS-Namen ausstellen:

1. Im [Tailscale Admin-Panel](https://login.tailscale.com/admin/dns)
   unter "DNS" → "HTTPS Certificates" aktivieren (einmalig).
2. Auf dem PC:
   ```powershell
   tailscale cert <dein-hostname>.<dein-tailnet>.ts.net
   ```
   (den genauen Namen zeigt `tailscale status`). Erzeugt eine `.crt`-
   und `.key`-Datei im aktuellen Verzeichnis.
3. Vite-Dev-Server mit diesem Zertifikat starten (z. B. in
   `frontend/vite.config.ts` unter `server.https` auf die beiden
   Dateipfade zeigen lassen) und/oder das Backend mit
   `--ssl-certfile`/`--ssl-keyfile` (uvicorn unterstützt das nativ).
4. Auf dem Handy dann `https://<hostname>.<tailnet>.ts.net:5173`
   statt der nackten IP verwenden.

Das ist bewusst **nicht** automatisch verdrahtet (Zertifikatspfade sind
pro Tailnet/Hostname unterschiedlich, die Vite-/Uvicorn-Config-Änderung
hängt von deinem konkreten Zertifikat-Speicherort ab) - siehe "Kein
Scope-Creep" unten. Ohne diesen Schritt ist Cypher über Tailscale
trotzdem voll nutzbar, nur eben ohne Mikrofon vom Handy aus.

## 4. PWA aufs Handy holen

1. PC: Frontend starten (`cd frontend && npm run dev` - lauscht auf
   allen Interfaces standardmäßig, erreichbar über die Tailscale-IP).
2. Handy (im selben Tailnet, Tailscale-App muss aktiv sein):
   Browser öffnen, `http://100.x.y.z:5173` aufrufen (deine Tailscale-IP
   aus Schritt 1, nicht `localhost`).
3. **Android (Chrome):** Menü (⋮) → "App installieren" / "Zum
   Startbildschirm hinzufügen".
   **iPhone (Safari):** Teilen-Symbol → "Zum Home-Bildschirm".
   (Nur Safari kann auf iOS PWAs installieren, Chrome/Firefox auf iOS
   nicht - das ist eine iOS-Einschränkung, keine Cypher-Einschränkung.)
4. App vom Home-Bildschirm aus öffnen - läuft jetzt ohne Browser-Chrome
   (`display: standalone`), mit korrektem Cypher-Icon und -Namen.
5. Voice-UI zeigt sofort das Lokal/Cloud-Badge (Phase 7) - Bestätigungen
   für `write_file` funktionieren identisch zum PC-Browser, inklusive
   Sprachantwort "ja"/"nein".

**Worauf achten:**
- Beide Geräte brauchen eine aktive Internetverbindung (Tailscale
  braucht sie initial für die Koordination, auch wenn die eigentliche
  Verbindung danach direkt zwischen den Geräten läuft).
- Tailscale-App auf dem Handy muss laufen (nicht zwingend im
  Vordergrund, aber nicht "abgemeldet").
- Ändert sich die Tailscale-IP des PCs (selten, aber möglich), muss
  `.env` (`API_HOST`, `CORS_ORIGINS`) und die URL auf dem Handy
  angepasst werden - oder direkt den stabilen MagicDNS-Namen
  (`tailscale status` zeigt ihn, Format `<hostname>.<tailnet>.ts.net`)
  statt der nackten IP verwenden, dann bleibt die Adresse fix.

## 5. Was automatisiert geprüft wurde vs. was du manuell verifizieren musst

Echtes Handy + echtes Tailnet lassen sich in dieser Sandbox nicht
nachstellen - hier die ehrliche Trennung:

**Automatisiert getestet (257 Backend-, 61 Frontend-Tests weiterhin
grün, plus neu für Phase 8):**
- `_check_tokens`: verweigert den Start bei Platzhalter-Token +
  nicht-lokalem `API_HOST` (`0.0.0.0`, eine Beispiel-Tailscale-IP, eine
  LAN-IP), warnt nur (kein Hard-Fail) bei `127.0.0.1` - Unit-Tests in
  `test_auth_security.py`.
- CORS-Konfiguration: `cors_origins_list` parst mehrere Origins korrekt,
  keine Wildcard-Logik vorhanden.
- `run.py` liest `API_HOST`/`API_PORT` tatsächlich aus den Settings
  (durch Code-Inspektion + bestehende Settings-Tests abgedeckt - kein
  eigener neuer Test, da `run.py` nur `uvicorn.run()` mit den bereits
  getesteten Settings-Werten aufruft, ohne eigene Logik).
- Bestehende Approval-Gate-/Tool-Calling-/Cloud-Toggle-Tests: unverändert
  grün, da diese Phase keine Zeile davon anfasst.

**Musst du manuell auf deinem echten Setup verifizieren:**
1. `scripts\check_tailscale.ps1` laufen lassen - meldet es deine echte
   Tailscale-IP?
2. `.env` mit dieser IP + echten Tokens füllen, `python run.py`
   starten - startet er, oder verweigert er sich (falls du testweise
   einen Platzhalter-Token stehen lässt, sollte er sich verweigern -
   das wäre der Beweis, dass die Härtung live greift)?
3. Vom PC selbst: `curl http://<tailscale-ip>:8000/health` - sollte
   `{"status":"ok"}` liefern.
4. **Vom Handy, mit deinem WLAN (nicht Tailscale) - Tailscale-App
   kurz beenden/Tailnet verlassen:** derselbe Request sollte fehlschlagen
   (Timeout/Connection refused) - das ist der eigentliche Beweis, dass
   die Bind-Adresse tatsächlich einschränkt und nicht doch `0.0.0.0`
   irgendwo hängt.
5. Vom Handy, Tailscale aktiv: PWA installieren (Schritt 4 oben). Ohne
   HTTPS-Setup (Abschnitt 3b) ist das Mikrofon nicht verfügbar - dann
   stattdessen über den Chat-Text-Eingabe-Weg einen `write_file`-Request
   auslösen und den Bestätigungs-Dialog per Ja/Nein-Button durchklicken.
   Mit HTTPS-Setup: zusätzlich den gesprochenen "ja"/"nein"-Weg der
   Voice-UI testen.
6. Provider auf "Cloud" umschalten (Settings), prüfen ob das Lokal/Cloud-
   Badge auf dem Handy korrekt "☁ Cloud" zeigt.
7. PWA vom Home-Bildschirm starten (nicht im Browser-Tab) und in den
   Settings prüfen, ob der "mobiler Kontext"-Hinweis aus Phase 7
   erscheint (er sollte, sobald `display-mode: standalone` greift).

## Bewusst nicht gebaut (kein Scope-Creep)

- Keine automatische Tailscale-Erkennung mit automatischem
  Cloud-Vorschlag/-Wechsel - Phase 7s Regel ("nie automatisch") gilt
  unverändert, dieser Punkt ist für eine spätere Phase vorgemerkt, falls
  gewünscht.
- Kein zweites Shared-Secret/API-Key-System (siehe Abschnitt 3).
- Keine Änderung an Approval-Gate, ToolExecutor oder den PC-Steuerungs-
  Tools aus Phase 6.
- Tailscale ACLs/Funnel/Serve (Tailscales eigene Reverse-Proxy-Features)
  nicht konfiguriert - für den beschriebenen Ein-Nutzer-Anwendungsfall
  reicht direkter IP-Zugriff im eigenen Tailnet.
