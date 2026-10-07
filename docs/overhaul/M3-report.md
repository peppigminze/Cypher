# M3-Report – Voice-Pipeline (IN ARBEIT, nicht abgeschlossen)

Stand beim Abbruch (Nutzungslimit):

## Funktioniert (real über WebSocket getestet, `backend/eval/voice_client.py`)
- Streaming-Engine (`app/agent/engine.py`): ein Agent für Voice, Text und /api/tool-chat; satzweises TTS, Abbruch, Verlauf, Freigaben.
- Voice-Session (`app/voice/session.py`, `/api/voice/ws`): VAD (Silero v6), Endpunkt-Erkennung, spekulative STT, Wake-Word „Hey Cypher" (Whisper tiny + phonetischer Abgleich), Barge-in (Tap: Stopp nach 6 ms; Wake-Word: 1,1 s), Echo-Schutz (eigene Antwort mit „Cypher" löst nichts aus), angesagte Timer.
- Latenz vom Sprechende bis zum ersten Audio: vorher 2,5 s (ohne VAD, manuell) → jetzt 1,65–1,8 s ohne Tool (ab Turn 2), erster Turn einer Session ~2,6 s (Hybrid-Modell, Prompt-Cache greift erst ab Turn 2).

## Offen
- **Bug:** qwen3.5:9b behauptet bei „Schreib eine Notiz …" „Notiz gespeichert", ohne `save_note` aufzurufen. Das muss vor der Abnahme behoben werden (Prompt-/Tool-Beschreibung + Guard „Erfolg nur nach Tool-Ergebnis").
- Frontend-Audio-Client (AudioWorklet + WebSocket), Unit-Tests für Session/Engine/VAD/Wake, Report fertigstellen.
