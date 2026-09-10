"""
Local speech-to-text via faster-whisper (CTranslate2) - fully offline,
no cloud call, no API key required. Separate from app/llm/* on purpose:
this is STT, not a chat-completion backend, and doesn't share that
interface.

Model weights are downloaded once from Hugging Face on first use and
then cached (~/.cache/huggingface/hub) - no re-download on later runs.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

from faster_whisper import WhisperModel

from app.config import Settings, get_settings

logger = logging.getLogger("cypher.stt.whisper")


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    audio_duration_seconds: float  # length of the submitted audio
    transcribe_time_seconds: float  # wall-clock time faster-whisper took


@lru_cache
def _load_model(model_size: str, device: str, compute_type: str) -> WhisperModel:
    """The WhisperModel is expensive to construct (loads model weights
    into memory) - lru_cache means this only runs once per (size,
    device, compute_type) combination. The first /transcribe call after
    backend startup pays that one-time cost; every call after reuses
    the already-loaded model."""
    logger.info(
        "Loading faster-whisper model (size=%s, device=%s, compute_type=%s) - "
        "first transcription after startup will be slower while this loads.",
        model_size,
        device,
        compute_type,
    )
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def get_whisper_model(settings: Settings | None = None) -> WhisperModel:
    settings = settings or get_settings()
    return _load_model(settings.WHISPER_MODEL_SIZE, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE)


def transcribe_audio(audio_bytes: bytes, settings: Settings | None = None) -> TranscriptionResult:
    """Transcribe raw audio bytes to text. Accepts whatever format PyAV
    (bundled with faster-whisper) can decode - webm/opus straight from
    the browser's MediaRecorder, wav, mp3, m4a, ... - no manual
    conversion needed on the caller's side.

    This is synchronous/CPU-bound. Callers on the async request path
    must run it via asyncio.to_thread() so a transcription doesn't
    block the event loop for other requests in the meantime.
    """
    model = get_whisper_model(settings)
    started = time.monotonic()
    segments, info = model.transcribe(io.BytesIO(audio_bytes), beam_size=5)
    text = "".join(segment.text for segment in segments).strip()
    elapsed = time.monotonic() - started
    return TranscriptionResult(
        text=text,
        language=info.language,
        audio_duration_seconds=info.duration,
        transcribe_time_seconds=elapsed,
    )
