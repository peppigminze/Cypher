"""
Local speech-to-text via faster-whisper (CTranslate2) on the CPU.

CPU on purpose: CTranslate2 has no AMD GPU support on Windows, and the GPU
is reserved for the LLM anyway (no VRAM competition). Settings come from
the M0 benchmark (docs/overhaul/AUDIT.md section 6): model "small", int8,
8 threads, beam 5, fixed language, an initial prompt with proper nouns.

Model weights are downloaded once from Hugging Face and cached.
"""
from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Union

import numpy as np
from faster_whisper import WhisperModel

from app.config import Settings, get_settings
from app.core.metrics import metrics

logger = logging.getLogger("cypher.stt.whisper")

AudioInput = Union[bytes, np.ndarray]

# Process-wide flag for /api/health ("is the model in memory yet?").
_loaded_keys: set[tuple] = set()
# CTranslate2 models are thread-safe for inference, but loading the same
# model twice in parallel (warm-up + first request) wastes ~1 s and RAM.
_load_lock = threading.Lock()


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    audio_duration_seconds: float
    transcribe_time_seconds: float


def _key(settings: Settings, size: str | None = None) -> tuple:
    return (size or settings.WHISPER_MODEL_SIZE, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE, settings.WHISPER_CPU_THREADS)


@lru_cache
def _load_model(model_size: str, device: str, compute_type: str, cpu_threads: int) -> WhisperModel:
    logger.info("Loading faster-whisper model (size=%s, device=%s, compute_type=%s, threads=%s)", model_size, device, compute_type, cpu_threads)
    started = time.perf_counter()
    model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=cpu_threads)
    logger.info("faster-whisper %s loaded in %.1fs", model_size, time.perf_counter() - started)
    return model


def get_whisper_model(settings: Settings | None = None, size: str | None = None) -> WhisperModel:
    settings = settings or get_settings()
    key = _key(settings, size)
    with _load_lock:
        model = _load_model(*key)
        _loaded_keys.add(key)
    return model


def is_loaded(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return _key(settings) in _loaded_keys


def warm_up(settings: Settings | None = None) -> float:
    """Load the model and run one tiny inference so the first real
    request doesn't pay for lazy initialization. Returns seconds taken."""
    settings = settings or get_settings()
    started = time.perf_counter()
    model = get_whisper_model(settings)
    silence = np.zeros(16000, dtype=np.float32)
    list(model.transcribe(silence, language=settings.WHISPER_LANGUAGE or None, beam_size=1)[0])
    return time.perf_counter() - started


def transcribe_audio(audio: AudioInput, settings: Settings | None = None, *, size: str | None = None, initial_prompt: str | None = None) -> TranscriptionResult:
    """Transcribe audio to text.

    `audio` is either encoded bytes in any format PyAV can decode (webm/opus
    from MediaRecorder, wav, mp3, ...) or a float32 mono 16 kHz numpy array
    (the streaming voice path). Synchronous and CPU-bound - call via
    asyncio.to_thread() from async code.
    """
    settings = settings or get_settings()
    model = get_whisper_model(settings, size)
    source = io.BytesIO(audio) if isinstance(audio, (bytes, bytearray)) else audio
    started = time.monotonic()
    segments, info = model.transcribe(
        source,
        language=settings.WHISPER_LANGUAGE or None,
        beam_size=settings.WHISPER_BEAM_SIZE,
        initial_prompt=(initial_prompt if initial_prompt is not None else settings.WHISPER_INITIAL_PROMPT) or None,
        # Whisper hallucinates text on silence ("Untertitel im Auftrag des
        # ZDF ..."); the VAD filter drops non-speech before decoding.
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        without_timestamps=True,
    )
    text = "".join(segment.text for segment in segments).strip()
    elapsed = time.monotonic() - started
    metrics.record("stt", elapsed)
    return TranscriptionResult(text=text, language=info.language, audio_duration_seconds=info.duration, transcribe_time_seconds=elapsed)
