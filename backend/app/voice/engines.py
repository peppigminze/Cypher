"""Adapters that put the existing Whisper/Piper code behind the voice protocols."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import numpy as np

from app.config import Settings
from app.core.metrics import metrics
from app.voice.protocols import SAMPLE_RATE, SpeechAudio, Transcript
from app.voice.speech_text import to_speakable


class WhisperSTT:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, audio: np.ndarray, *, initial_prompt: Optional[str] = None) -> Transcript:
        from app.stt.whisper_provider import transcribe_audio  # noqa: PLC0415

        result = await asyncio.to_thread(transcribe_audio, audio, self.settings, initial_prompt=initial_prompt, vad=False)
        return Transcript(text=result.text, language=result.language, audio_seconds=audio.shape[0] / SAMPLE_RATE, seconds=result.transcribe_time_seconds)


class PiperTTS:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _synth(self, text: str) -> SpeechAudio:
        from app.tts.piper_provider import get_tts  # noqa: PLC0415

        started = time.perf_counter()
        audio = get_tts(self.settings).generate(text, sid=self.settings.PIPER_SPEAKER_ID, speed=self.settings.PIPER_SPEED)
        samples = np.clip(np.asarray(audio.samples, dtype=np.float32), -1.0, 1.0)
        pcm16 = (samples * 32767).astype("<i2").tobytes()
        elapsed = time.perf_counter() - started
        metrics.record("tts", elapsed)
        return SpeechAudio(pcm16=pcm16, sample_rate=audio.sample_rate, seconds=len(samples) / float(audio.sample_rate), synth_seconds=elapsed)

    async def synthesize(self, text: str) -> SpeechAudio:
        speakable = to_speakable(text)
        if not speakable:
            return SpeechAudio(pcm16=b"", sample_rate=22050, seconds=0.0, synth_seconds=0.0)
        return await asyncio.to_thread(self._synth, speakable)
