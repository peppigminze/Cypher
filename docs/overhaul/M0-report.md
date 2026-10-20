# M0-Report – Audit & Plan

**Ergebnis:** [AUDIT.md](AUDIT.md) und [PLAN.md](PLAN.md). Noch kein Feature-Code.

## Was gemacht wurde
- Branch `overhaul/v1` angelegt; 43 uncommittete Dateien als Baseline `69dfbe8` gesichert.
- Repo gelesen (Backend, Frontend, Skripte, Doku), Abweichungen dokumentiert.
- Hardware real geprüft (CIM + Perf-Counter + Ollama-Serverlog).
- Benchmark-Skripte angelegt und ausgeführt:
  - `backend/eval/bench_llm.py` + `backend/eval/tool_cases.json` (44 deutsche Fälle)
  - `backend/eval/bench_whisper.py`
- `docs/DEPENDENCIES.md` angelegt (Lizenzen aller Abhängigkeiten geprüft).

## Was real ausgeführt wurde
- `pytest`: **262 passed**; `vitest`: **61 passed**; `npm run build`: ok (180 kB JS, 57 kB gzip).
- Smoke-Tests gegen die laufende Instanz: `/health`, Auth 401, `/api/tool-chat` (Uhrzeit, Medien,
  Approval inkl. Ablehnung), `/speak`, `/transcribe`, `/api/usage`, `/api/settings/provider`.
- LLM-Benchmark über 6 Modelle, Mehr-Turn-Latenztest, Thinking-Probe, VRAM-Auslagerungs-Nachweis.
- Whisper-Benchmark (base, small, large-v3-turbo; 4/8/12 Threads; Beam 1/5; mit/ohne Prompt).
- End-to-End-Latenz der heutigen Pipeline (Vorher-Wert für M3): Median 2,52 s warm, 11,9 s kalt.

## Offen / nicht testbar
- Echte Mikrofonaufnahmen für die WER (nur synthetische Sprache) → `bench_whisper.py --wav-dir`.
- Cloud-Provider live (kein API-Key vorhanden).

## Seiteneffekte auf dem PC
- Smoke-Test „Pausiere die Musik" hat einmal die Play/Pause-Medientaste gesendet.
- Zusätzlich heruntergeladene Ollama-Modelle: `qwen3.5:9b`, `qwen3.5:4b`, `qwen3:14b`, `gemma4:12b`
  (zusammen ~27 GB). `qwen3:14b` und `gemma4:12b` werden nicht gebraucht und können mit
  `ollama rm qwen3:14b gemma4:12b` entfernt werden (nicht automatisch gelöscht).
