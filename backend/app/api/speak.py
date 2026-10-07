"""
Text-to-speech test endpoint (Piper voice via sherpa-onnx, fully
local/offline).

Same pattern as /transcribe (Phase 2): its own file, Bearer auth, and
it does not touch the production chat/message pipeline or the existing
browser-native voice output (frontend/src/hooks/useSpeechOutput.ts) at
all - this exists to exercise local Piper synthesis end to end before
anything is wired to replace the browser API.

Returns raw WAV bytes directly (not JSON/base64) to prioritize latency -
the browser can start playing/buffering audio/wav immediately, and
skipping a base64 round-trip avoids ~33% extra payload size and an
extra decode step. Timing metadata rides along as response headers
instead of a JSON envelope for the same reason.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Response

from app.auth import require_user
from app.core import errors
from app.schemas import SpeakIn
from app.tts.piper_provider import VoiceNotFoundError, synthesize_speech

logger = logging.getLogger("cypher.api.speak")

router = APIRouter(tags=["speak"])


@router.post("/speak")
async def speak(
    payload: SpeakIn,
    _user=Depends(require_user),
) -> Response:
    logger.info("SPEAK REQUEST -> text_length=%d", len(payload.text))

    try:
        # CPU-bound - offload to a worker thread so one synthesis doesn't
        # stall every other request the event loop is serving.
        result = await asyncio.to_thread(synthesize_speech, payload.text)
    except VoiceNotFoundError as exc:
        raise errors.tts_voice_missing() from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Speech synthesis failed")
        raise errors.tts_failed() from exc

    logger.info(
        "SPEAK RESULT -> audio_duration=%.2fs generation_time=%.2fs sample_rate=%d",
        result.audio_duration_seconds,
        result.generation_time_seconds,
        result.sample_rate,
    )

    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Audio-Duration-Seconds": f"{result.audio_duration_seconds:.3f}",
            "X-Generation-Time-Seconds": f"{result.generation_time_seconds:.3f}",
            "X-Sample-Rate": str(result.sample_rate),
        },
    )
