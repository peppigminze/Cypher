"""
Local text-to-speech via a Piper VITS voice run through sherpa-onnx -
fully offline, no cloud call, no API key. Same shape as
app/stt/whisper_provider.py on purpose (lazy-loaded, cached model
singleton; a plain dataclass result).

Deliberately uses sherpa-onnx (Apache-2.0) with the raw Piper ONNX voice
files instead of the official `piper-tts` PyPI package, whose current
piper1-gpl backend is GPL-3 licensed - this keeps the project free of
that licensing obligation (same reasoning the personalJarvis reference
project uses for its own Piper integration).

Voice files (~78 MB) are not committed to git - run
backend/scripts/download_piper_voice.py once per machine first.
"""
from __future__ import annotations

import io
import logging
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import sherpa_onnx

from app.config import Settings, get_settings
from app.core.metrics import metrics

logger = logging.getLogger("cypher.tts.piper")

# backend/app/tts/piper_provider.py -> backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class VoiceNotFoundError(Exception):
    """Raised when the configured Piper voice directory/files are missing
    (most likely: download_piper_voice.py hasn't been run yet)."""


@dataclass
class SynthesisResult:
    wav_bytes: bytes
    sample_rate: int
    audio_duration_seconds: float
    generation_time_seconds: float


def _resolve_model_dir(raw_dir: str) -> Path:
    """Relative paths resolve against backend/, not the launching
    process's CWD - same reasoning as _resolve_database_url() in
    app/database/db.py (backend and the PC agent are started from
    different working directories)."""
    path = Path(raw_dir)
    return path if path.is_absolute() else (_BACKEND_DIR / path)


@lru_cache
def _load_tts(model_dir: str, num_threads: int) -> sherpa_onnx.OfflineTts:
    """Expensive to construct (loads the ONNX voice + espeak-ng phoneme
    data into memory) - lru_cache means this happens once per (model_dir,
    num_threads) combination. The first /speak call after startup pays
    that cost; every call after reuses the loaded model."""
    voice_dir = _resolve_model_dir(model_dir)
    onnx_path = voice_dir / f"{voice_dir.name}.onnx"
    tokens_path = voice_dir / "tokens.txt"
    data_dir = voice_dir / "espeak-ng-data"

    if not onnx_path.exists() or not tokens_path.exists():
        raise VoiceNotFoundError(
            f"Piper voice not found at '{voice_dir}'. Run "
            "'python scripts/download_piper_voice.py' from backend/ first."
        )

    logger.info("Loading Piper voice from %s - first synthesis after startup will be slower while this loads.", voice_dir)
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(onnx_path),
        tokens=str(tokens_path),
        data_dir=str(data_dir),
    )
    model_config = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=num_threads, provider="cpu")
    return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model_config))


_loaded_keys: set[tuple] = set()


def get_tts(settings: Settings | None = None) -> sherpa_onnx.OfflineTts:
    settings = settings or get_settings()
    key = (settings.PIPER_MODEL_DIR, settings.PIPER_NUM_THREADS)
    tts = _load_tts(*key)
    _loaded_keys.add(key)
    return tts


def voice_files_present(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    voice_dir = _resolve_model_dir(settings.PIPER_MODEL_DIR)
    return (voice_dir / f"{voice_dir.name}.onnx").exists() and (voice_dir / "tokens.txt").exists()


def is_loaded(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return (settings.PIPER_MODEL_DIR, settings.PIPER_NUM_THREADS) in _loaded_keys


def warm_up(settings: Settings | None = None) -> float:
    """Load the voice and synthesize one word - the first generate() call
    after loading is ~20x slower than later ones (ONNX initialization)."""
    settings = settings or get_settings()
    started = time.perf_counter()
    get_tts(settings).generate("Hallo.", sid=settings.PIPER_SPEAKER_ID, speed=settings.PIPER_SPEED)
    return time.perf_counter() - started


def _pcm16_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """sherpa-onnx returns float32 samples in [-1, 1] - encode as a
    standard 16-bit PCM mono WAV, the format every browser <audio>
    element and the frontend test page can play without extra decoding."""
    samples_i16 = np.clip(samples, -1.0, 1.0)
    samples_i16 = (samples_i16 * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples_i16.tobytes())
    return buffer.getvalue()


def synthesize_speech(text: str, settings: Settings | None = None) -> SynthesisResult:
    """Synthesize `text` to speech and return it as WAV bytes.

    CPU-bound and synchronous - callers on the async request path must
    run this via asyncio.to_thread() so a synthesis doesn't block the
    event loop for other requests in the meantime.
    """
    settings = settings or get_settings()
    tts = get_tts(settings)

    started = time.monotonic()
    audio = tts.generate(text, sid=settings.PIPER_SPEAKER_ID, speed=settings.PIPER_SPEED)
    elapsed = time.monotonic() - started
    metrics.record("tts", elapsed)

    samples = np.asarray(audio.samples, dtype=np.float32)
    wav_bytes = _pcm16_wav_bytes(samples, audio.sample_rate)
    audio_duration = len(samples) / float(audio.sample_rate)

    return SynthesisResult(
        wav_bytes=wav_bytes,
        sample_rate=audio.sample_rate,
        audio_duration_seconds=audio_duration,
        generation_time_seconds=elapsed,
    )
