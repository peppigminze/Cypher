"""
Wake word "Hey Cypher" without a trained model.

Why not openWakeWord (the usual choice): its code is Apache-2.0, but all
pre-trained models are CC BY-NC-SA 4.0 (not permissive) and there is no
"Hey Cypher" model - a custom one needs a training run, which is out of
scope. See docs/overhaul/AUDIT.md / PLAN.md.

So this is a keyword spotter built from parts that already exist:
    VAD found an utterance -> the first ~2.5 s go through a small Whisper
    model with the wake phrase as initial prompt -> the transcript's first
    words are fuzzy-matched against the wake phrase after a light phonetic
    normalisation (Whisper hears "Cypher" as "Saifer", "Sypher", "Süfer" ...).

Honest trade-offs vs. a trained detector: it only reacts after the
utterance (or its first 2.5 s) ended, it costs ~0.3-0.4 s of CPU per
utterance heard, and every utterance in the room is transcribed locally
(nothing leaves the PC). It is behind the WakeWordDetector protocol, so a
trained model can replace it without touching the session code.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

import numpy as np

from app.voice.protocols import SAMPLE_RATE, WakeResult

logger = logging.getLogger("cypher.voice.wake")

_FILLERS = {"hey", "hej", "hei", "hi", "he", "ey", "ei", "eh", "ok", "okay", "hallo", "hallöchen", "yo", "na"}


def phonetic(word: str) -> str:
    """Rough German-ish phonetic key: makes 'Cypher', 'Saifer', 'Sypher',
    'Zeifer' land close together."""
    w = unicodedata.normalize("NFKD", word.lower())
    w = "".join(ch for ch in w if not unicodedata.combining(ch))
    w = re.sub(r"[^a-z]", "", w)
    for src, dst in (("ph", "f"), ("v", "f"), ("ck", "k"), ("c", "s"), ("z", "s"), ("y", "i"), ("ei", "ai"), ("ey", "ai"), ("ie", "i"), ("ü", "i"), ("u", "i"), ("w", "f")):
        w = w.replace(src, dst)
    w = re.sub(r"(.)\1+", r"\1", w)  # collapse doubled letters
    return w


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _words(text: str) -> List[Tuple[str, int]]:
    """Words with their end offset in `text`."""
    return [(m.group(0), m.end()) for m in re.finditer(r"[\wäöüÄÖÜß']+", text)]


def match_wake_phrase(transcript: str, phrase: str, threshold: float) -> WakeResult:
    """Find the wake phrase at the START of `transcript`.

    Only the start counts: "ich hab mit Cypher geredet" mid-sentence must
    not wake the assistant. Two comparisons, best score wins:
      * the core word(s) ("cypher") after optional fillers ("hey", "okay")
      * the whole phrase incl. filler against the first 1-3 words - Whisper
        often merges "Hey Saifer" into one word ("Heiseifer").
    """
    words = _words(transcript)
    phrase_words = re.findall(r"[\wäöüÄÖÜß]+", phrase.lower())
    core_words = [w for w in phrase_words if w not in _FILLERS] or phrase_words
    core = phonetic("".join(core_words))
    full = phonetic("".join(phrase_words))
    best_score, best_end = 0.0, 0
    for start in range(min(3, len(words))):
        if start > 0 and words[start - 1][0].lower() not in _FILLERS:
            break
        for width in (1, 2):
            if start + width > len(words):
                break
            candidate = phonetic("".join(w for w, _ in words[start : start + width]))
            if candidate:
                score = _similarity(candidate, core)
                if score > best_score:
                    best_score, best_end = score, words[start + width - 1][1]
    if full != core:
        for width in (1, 2, 3):
            if width > len(words):
                break
            candidate = phonetic("".join(w for w, _ in words[:width]))
            score = _similarity(candidate, full)
            if score > best_score:
                best_score, best_end = score, words[width - 1][1]
    if best_score < threshold and len(words) == 2 and len(words[0][0]) <= 5:
        # The utterance is just "<short word> Cypher": a misheard "Hey"
        # ("Karl Cypher.", "Ey Cypher") - but not "Karl Cypher kommt morgen".
        score = _similarity(phonetic(words[1][0]), core)
        if score >= threshold:
            best_score, best_end = score, words[1][1]
    detected = best_score >= threshold
    remainder = transcript[best_end:].strip(" ,.!?;:-–—") if detected else ""
    return WakeResult(detected=detected, score=round(best_score, 3), transcript=transcript, remainder=remainder)


class WhisperWakeWord:
    """WakeWordDetector implementation (see module docstring)."""

    def __init__(self, phrase: str, model_size: str, threshold: float, window_seconds: float, stt_fn=None) -> None:
        self.phrase = phrase
        self.model_size = model_size
        self.threshold = threshold
        self.window_samples = int(window_seconds * SAMPLE_RATE)
        self._stt_fn = stt_fn

    def _transcribe(self, audio: np.ndarray) -> str:
        if self._stt_fn is not None:
            return self._stt_fn(audio)
        from app.stt.whisper_provider import transcribe_audio  # noqa: PLC0415

        return transcribe_audio(audio, size=self.model_size, initial_prompt=f"{self.phrase}.", vad=False, beam_size=1).text

    async def detect(self, audio: np.ndarray, window_samples: Optional[int] = None) -> WakeResult:
        head = audio[: window_samples or self.window_samples]
        text = await asyncio.to_thread(self._transcribe, head)
        result = match_wake_phrase(text, self.phrase, self.threshold)
        logger.debug("wake check: %r -> detected=%s score=%.2f", text, result.detected, result.score)
        return result
