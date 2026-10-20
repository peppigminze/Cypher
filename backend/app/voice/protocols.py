"""
Small interfaces for every swappable voice component (Python `Protocol`s =
structural typing: any class with these methods fits, no inheritance).

Swapping e.g. Piper for another TTS, or the Whisper keyword spotter for a
trained wake-word model, means writing one class with the same methods
and changing one line in app/voice/factory.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

import numpy as np

SAMPLE_RATE = 16_000  # everything inbound is 16 kHz mono float32 in [-1, 1]


@dataclass
class Transcript:
    text: str
    language: Optional[str]
    audio_seconds: float
    seconds: float


@dataclass
class SpeechAudio:
    pcm16: bytes  # little-endian int16 mono
    sample_rate: int
    seconds: float
    synth_seconds: float


@dataclass
class WakeResult:
    detected: bool
    score: float
    transcript: str
    # Text spoken after the wake phrase in the same breath
    # ("Hey Cypher, wie spät ist es" -> "wie spät ist es").
    remainder: str = ""


@runtime_checkable
class SpeechToText(Protocol):
    async def transcribe(self, audio: np.ndarray, *, initial_prompt: Optional[str] = None) -> Transcript: ...


@runtime_checkable
class TextToSpeech(Protocol):
    async def synthesize(self, text: str) -> SpeechAudio: ...


@runtime_checkable
class VoiceActivityDetector(Protocol):
    frame_samples: int

    def speech_probability(self, frame: np.ndarray) -> float: ...

    def reset(self) -> None: ...


@runtime_checkable
class WakeWordDetector(Protocol):
    async def detect(self, audio: np.ndarray) -> WakeResult: ...
