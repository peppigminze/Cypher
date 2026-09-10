"""
Speech-to-text test endpoint (faster-whisper, fully local/offline).

Same pattern as /api/tool-chat (Phase 1): its own file, Bearer auth,
and it does not touch the production chat/message pipeline or the
existing browser-native voice input (frontend/src/hooks/useVoiceInput.ts)
at all - this exists to exercise local Whisper transcription end to end
before anything is wired to replace the browser API.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth import require_user
from app.stt.whisper_provider import transcribe_audio
from app.schemas import TranscribeOut

logger = logging.getLogger("cypher.api.transcribe")

router = APIRouter(tags=["transcribe"])

# A silent/near-empty recording is still a few KB of container overhead;
# anything smaller than this is almost certainly a broken upload, not audio.
MIN_AUDIO_BYTES = 100
# Generous cap on one voice message - plenty for a spoken request, small
# enough that a runaway/looping recording can't exhaust server memory.
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/transcribe", response_model=TranscribeOut)
async def transcribe(
    audio: UploadFile = File(...),
    _user=Depends(require_user),
) -> TranscribeOut:
    audio_bytes = await audio.read()
    logger.info(
        "TRANSCRIBE REQUEST -> filename=%s content_type=%s bytes=%d",
        audio.filename,
        audio.content_type,
        len(audio_bytes),
    )

    if len(audio_bytes) < MIN_AUDIO_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file is empty or too small.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio file exceeds the 25 MB limit.")

    try:
        # CPU-bound - offload to a worker thread so one transcription
        # doesn't stall every other request the event loop is serving.
        result = await asyncio.to_thread(transcribe_audio, audio_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcription failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Transcription failed: {exc}") from exc

    logger.info(
        "TRANSCRIBE RESULT -> text=%r language=%s audio_duration=%.2fs transcribe_time=%.2fs",
        result.text,
        result.language,
        result.audio_duration_seconds,
        result.transcribe_time_seconds,
    )

    return TranscribeOut(
        text=result.text,
        language=result.language,
        audio_duration_seconds=result.audio_duration_seconds,
        transcribe_time_seconds=result.transcribe_time_seconds,
    )
