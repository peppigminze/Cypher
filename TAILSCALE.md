# Cypher über Tailscale

Diese Anleitung ist umgezogen: **[docs/MOBILE.md](docs/MOBILE.md)**.

Seit der Überholung (M7) läuft der Handy-Zugriff über `tailscale serve`
(HTTPS mit echtem Zertifikat, damit das Mikrofon geht). Das Backend bleibt
dabei auf `127.0.0.1`. Die frühere Variante mit `API_HOST=<Tailscale-IP>`
und dem Vite-Dev-Server auf Port 5173 ist nicht mehr nötig.
