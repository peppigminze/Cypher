"""
Endpointing: turns a continuous 16 kHz stream into utterances using VAD.

    feed(chunk) -> list of events:
        SpeechStart              speech began (incl. pre-roll before it)
        SpeechEnd(audio, ...)    utterance finished: silence long enough,
                                 or the max length was reached

Rules (all configurable, see VAD_* settings):
  * speech starts after `start_frames` consecutive frames >= threshold,
    so a single click doesn't open an utterance;
  * pre-roll (default 300 ms) before the start is kept, otherwise the first
    syllable ("Hey") would be cut;
  * speech ends after `silence_ms` below a lower threshold (hysteresis),
    so breathing pauses inside a sentence don't end it;
  * utterances shorter than `min_speech_ms` are dropped as noise.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Union

import numpy as np

from app.voice.protocols import SAMPLE_RATE, VoiceActivityDetector


@dataclass
class SpeechStart:
    pass


@dataclass
class SpeechPause:
    """A short pause inside an utterance (not yet its end). Lets the caller
    start transcribing speculatively while the endpoint is still pending."""

    audio: np.ndarray
    voiced_frames: int


@dataclass
class SpeechEnd:
    audio: np.ndarray
    duration_seconds: float
    # True if the utterance was cut at the max length (speaker still talking).
    truncated: bool = False
    voiced_frames: int = 0


SegmentEvent = Union[SpeechStart, SpeechPause, SpeechEnd]


class Endpointer:
    def __init__(
        self,
        vad: VoiceActivityDetector,
        *,
        threshold: float = 0.5,
        silence_ms: int = 700,
        min_speech_ms: int = 250,
        preroll_ms: int = 300,
        max_utterance_seconds: float = 20.0,
        start_frames: int = 2,
        early_pause_ms: int = 0,
    ) -> None:
        self.vad = vad
        self.frame = vad.frame_samples
        frame_ms = self.frame * 1000 / SAMPLE_RATE
        self.start_threshold = threshold
        self.end_threshold = max(0.05, threshold - 0.15)
        self.silence_frames = max(1, int(silence_ms / frame_ms))
        self.min_speech_frames = max(1, int(min_speech_ms / frame_ms))
        self.preroll_frames = int(preroll_ms / frame_ms)
        self.max_frames = int(max_utterance_seconds * 1000 / frame_ms)
        self.start_frames = start_frames
        # 0 disables SpeechPause events.
        self.early_pause_frames = int(early_pause_ms / frame_ms) if early_pause_ms else 0
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll: Deque[np.ndarray] = deque(maxlen=max(1, self.preroll_frames))
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._preroll.clear()
        self.in_speech = False
        self._frames: List[np.ndarray] = []
        self._voiced = 0
        self._above = 0
        self._silence = 0
        self.last_probability = 0.0

    def feed(self, chunk: np.ndarray) -> List[SegmentEvent]:
        events: List[SegmentEvent] = []
        self._pending = np.concatenate([self._pending, chunk.astype(np.float32, copy=False)])
        while self._pending.shape[0] >= self.frame:
            frame, self._pending = self._pending[: self.frame], self._pending[self.frame :]
            event = self._process(frame)
            if event is not None:
                events.append(event)
        return events

    def _process(self, frame: np.ndarray) -> Optional[SegmentEvent]:
        prob = self.vad.speech_probability(frame)
        self.last_probability = prob
        if not self.in_speech:
            self._preroll.append(frame)
            self._above = self._above + 1 if prob >= self.start_threshold else 0
            if self._above >= self.start_frames:
                self.in_speech = True
                self._frames = list(self._preroll)
                self._voiced = self._above
                self._silence = 0
                return SpeechStart()
            return None

        self._frames.append(frame)
        if prob >= self.end_threshold:
            self._voiced += 1
            self._silence = 0
        else:
            self._silence += 1
            if self.early_pause_frames and self._silence == self.early_pause_frames and self._voiced >= self.min_speech_frames:
                return SpeechPause(audio=np.concatenate(self._frames), voiced_frames=self._voiced)
        if self._silence >= self.silence_frames:
            return self._finish(truncated=False)
        if len(self._frames) >= self.max_frames:
            return self._finish(truncated=True)
        return None

    def _finish(self, truncated: bool) -> Optional[SegmentEvent]:
        frames, voiced = self._frames, self._voiced
        self.in_speech = False
        self._frames = []
        self._voiced = 0
        self._above = 0
        self._silence = 0
        self._preroll.clear()
        if voiced < self.min_speech_frames:
            return None
        # Drop most of the trailing silence - it only costs STT time.
        keep_tail = min(len(frames), max(0, len(frames) - self.silence_frames + 3)) if not truncated else len(frames)
        audio = np.concatenate(frames[:keep_tail]) if keep_tail else np.zeros(0, dtype=np.float32)
        return SpeechEnd(audio=audio, duration_seconds=audio.shape[0] / SAMPLE_RATE, truncated=truncated, voiced_frames=voiced)

    def force_end(self) -> Optional[SpeechEnd]:
        """End the current utterance now (push-to-talk release)."""
        if not self.in_speech:
            return None
        event = self._finish(truncated=False)
        return event if isinstance(event, SpeechEnd) else None
