# Abhängigkeiten & Lizenzen

Regel: nur permissive Lizenzen (MIT / BSD / Apache-2.0 / ISC / PSF). Keine GPL/AGPL/LGPL.
Jede neue Abhängigkeit wird hier mit Lizenz und Grund eingetragen.
Geprüft über die installierten Paket-Metadaten (`importlib.metadata` bzw. `node_modules/*/package.json`).

## Backend (Python, direkte Abhängigkeiten)

| Paket | Version (installiert) | Lizenz | Wofür |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | HTTP-/WebSocket-API |
| uvicorn[standard] | 0.52.4 | BSD-3-Clause | ASGI-Server (inkl. `websockets` BSD-3, `httptools` MIT, `watchfiles` MIT) |
| pydantic | 2.13.5 | MIT | Datenvalidierung |
| pydantic-settings | 2.15.0 | MIT | Konfiguration aus `.env` |
| SQLAlchemy | 2.0.52 | MIT | Datenbank (SQLite) |
| python-dotenv | 1.2.3 | BSD-3-Clause | `.env`-Parsing |
| httpx | 0.28.1 | BSD-3-Clause | HTTP-Client (Ollama, Web-Tools) |
| psutil | 7.2.2 | BSD-3-Clause | Systeminfos |
| faster-whisper | 1.2.1 | MIT | Spracherkennung (bringt Silero-VAD-v6-ONNX mit, MIT) |
| ctranslate2 | 4.8.2 | MIT | Whisper-Inferenz (transitiv) |
| av (PyAV) | 18.1.0 | BSD-3-Clause | Audio-Decoding (transitiv) |
| onnxruntime | 1.29.0 | MIT | VAD-Inferenz (transitiv über faster-whisper) |
| python-multipart | 0.0.32 | Apache-2.0 | Datei-Uploads |
| sherpa-onnx | 1.13.8 | Apache-2.0 | Sprachausgabe (Piper-VITS) |
| numpy | 2.5.3 | BSD-3-Clause (+ MIT/Zlib/CC0 Teile) | Audio-Arrays |

Testwerkzeuge: pytest 9.1.1 (MIT), pytest-asyncio 1.4.0 (Apache-2.0).

Transitive Ausnahmen (kein GPL): `certifi` und `tqdm` stehen unter **MPL-2.0** (schwaches
Copyleft auf Dateiebene – betrifft nur Änderungen an deren eigenen Dateien, nicht Cypher).

## Frontend (npm)

| Paket | Version | Lizenz | Wofür |
|---|---|---|---|
| react / react-dom | 18.3.1 | MIT | UI |
| vite | 5.4.21 | MIT | Build/Dev-Server |
| @vitejs/plugin-react | 4.7.0 | MIT | React-Integration |
| vite-plugin-pwa | 0.20.5 | MIT | Service-Worker/Manifest |
| typescript | 5.9.3 | Apache-2.0 | Typprüfung |
| vitest | 2.1.9 | MIT | Unit-Tests |
| @testing-library/* | div. | MIT | Komponententests |
| jsdom | 25.0.1 | MIT | DOM für Tests |

## Modelle (keine Code-Abhängigkeiten, aber lizenzrelevant)

| Modell | Lizenz | Hinweis |
|---|---|---|
| qwen3.5:9b / qwen3.5:4b (Ollama) | Apache-2.0 | Standard-/Fallback-LLM |
| qwen2.5:7b | Apache-2.0 | bisheriges Standardmodell, weiter konfigurierbar |
| Whisper small/base (Systran faster-whisper Konvertierung) | MIT | STT |
| Silero VAD v6 | MIT | in faster-whisper enthalten |
| Piper de_DE-thorsten-medium | Datensatz CC0; feinabgestimmt von „lessac" (Blizzard-2013-Daten, nicht-kommerziell) | privat unkritisch |

## Bewusst **nicht** verwendet

| Paket | Grund |
|---|---|
| `piper-tts` (PyPI) | aktuelles Backend `piper1-gpl` ist GPL-3 → stattdessen sherpa-onnx |
| openWakeWord-Modelle | Code Apache-2.0, vortrainierte Modelle CC BY-NC-SA 4.0, kein „Hey Cypher" ohne Training |

## Änderungsprotokoll

| Meilenstein | Paket | Lizenz | Grund |
|---|---|---|---|
| M1 | tzdata 2026.5 | Apache-2.0 | IANA-Zeitzonen unter Windows (Einstellung `TIMEZONE`) |
| M2 | pycaw 20260927 (+ comtypes 1.4.17) | MIT / MIT | Systemlautstärke über Windows Core Audio (`set_volume`, `get_volume`) |
| M2 | winrt-runtime, winrt-Windows.Media.Control, -Foundation, -Foundation.Collections 3.2.1 | MIT | Windows-Mediensitzung: echtes Pause/Play statt Toggle, „Was läuft gerade?" |
| M2 | Pillow 12.3.0 | MIT-CMU (HPND, permissiv) | Bildschirmfoto für `describe_screen` (lokales Vision-Modell) |
| M3 | esbuild 0.21.5 (Frontend, devDependency; war schon transitiv über vite installiert) | MIT | Test baut das Mikrofon-AudioWorklet wie `vite build` minifiziert und prüft, dass es eigenständig läuft |
| M4 | – (keine neue Abhängigkeit) | – | Volltextsuche über SQLite FTS5 (in Pythons `sqlite3` enthalten, Public Domain) |
| M5 | openai 3.26.1 (+ httpx2 2.13.1, httpcore2 2.13.1, truststore, jiter 0.17.0, sniffio 1.3.1) | Apache-2.0 (httpx2/httpcore2 BSD-3-Clause, truststore MIT, jiter MIT, sniffio MIT/Apache-2.0) | Offizielles SDK für die Cloud (Responses API), ersetzt eigenen httpx-Code |
| M6 | eslint 10.11.0, @eslint/js 10.0.1, typescript-eslint 8.70.1, eslint-plugin-react-hooks 7.1.1, eslint-plugin-react-refresh 0.5.7, globals 17.12.0, prettier 3.9.9 (alle devDependencies) | MIT | Lint und Formatierung; Versionen bewusst ≥ 2 Wochen alt (Lieferketten-Vorsicht) |
| M6 | @playwright/test 1.63.0 (devDependency) | Apache-2.0 | Smoke-Test im vorhandenen Microsoft Edge (`channel: msedge`, kein Browser-Download) |
| M7 | segno 1.6.6 | BSD-3-Clause (LICENSE im Wheel geprüft) | QR-Code fürs Handy-Koppeln (Menü → Handy), reines Python, keine weiteren Abhängigkeiten |
| M8 | – (keine neue Abhängigkeit) | – | Abnahmetest nutzt nur vorhandene Pakete (httpx, websockets, numpy, openai) |

Externe Programme (keine Code-Abhängigkeit, vom Nutzer installiert):

| Programm | Lizenz | Wofür |
|---|---|---|
| Tailscale (optional) | Client BSD-3-Clause, Dienst mit kostenlosem Personal-Plan | Handy-Zugriff über `tailscale serve` (HTTPS im eigenen Tailnet), siehe docs/MOBILE.md |

Externe Dienste (keine Code-Abhängigkeit, ohne Account/Key):

| Dienst | Bedingungen | Wofür |
|---|---|---|
| DuckDuckGo HTML-Suche | öffentlich, nutzerausgelöst, geringe Frequenz | `web_search` (Alternative: eigene SearXNG-Instanz) |
| Open-Meteo | kostenlos für nicht-kommerzielle Nutzung, Daten CC BY 4.0 | `get_weather` |

