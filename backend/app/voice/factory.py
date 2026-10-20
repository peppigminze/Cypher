"""Builds the voice components from configuration - the one place to swap
an implementation (e.g. a trained wake-word model) for another."""
from __future__ import annotations

from typing import Optional

from app.agent.engine import ConversationEngine
from app.config import Settings
from app.llm.factory import get_provider_by_name
from app.tools.catalog import build_registry
from app.voice.engines import PiperTTS, WhisperSTT
from app.voice.segmenter import Endpointer
from app.voice.vad import SileroVAD
from app.voice.wakeword import WhisperWakeWord


def make_endpointer(settings: Settings) -> Endpointer:
    return Endpointer(
        SileroVAD(),
        threshold=settings.VAD_THRESHOLD,
        silence_ms=settings.VAD_SILENCE_MS,
        min_speech_ms=settings.VAD_MIN_SPEECH_MS,
        preroll_ms=settings.VAD_PREROLL_MS,
        max_utterance_seconds=settings.VAD_MAX_UTTERANCE_SECONDS,
        early_pause_ms=settings.VAD_EARLY_PAUSE_MS if settings.VAD_EARLY_PAUSE_MS < settings.VAD_SILENCE_MS else 0,
    )


def make_wake_word(settings: Settings) -> Optional[WhisperWakeWord]:
    if not settings.WAKE_WORD_ENABLED:
        return None
    return WhisperWakeWord(settings.WAKE_WORD_PHRASE, settings.WAKE_WORD_MODEL, settings.WAKE_WORD_THRESHOLD, settings.WAKE_WORD_WINDOW_SECONDS)


def make_engine_factory(settings: Settings, provider_name_fn):
    registry = build_registry()

    def factory(provider: Optional[str] = None) -> ConversationEngine:
        # Provider is looked up per turn: the local/cloud toggle takes effect
        # immediately, also in an already open voice session. `provider`
        # forces one (the user said "ja" to asking the cloud once).
        name = provider or provider_name_fn()
        return ConversationEngine(get_provider_by_name(name, settings), registry, provider_name=name, settings=settings)

    return factory


def make_stt(settings: Settings) -> WhisperSTT:
    return WhisperSTT(settings)


def make_tts(settings: Settings) -> PiperTTS:
    return PiperTTS(settings)
