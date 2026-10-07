"""
WebSocket endpoint for the live voice session: /api/voice/ws

Handshake:
  1. Origin check (browsers apply no CORS to WebSockets - see app/security/origin.py)
  2. per-IP connection rate limit
  3. the first message must be {"type": "auth", "token": "..."} within 5 s
     (browsers cannot set an Authorization header on WebSockets, and a
     token in the URL would end up in logs/history)
Then everything goes to app/voice/session.py.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import role_for_token
from app.config import get_settings
from app.core.context import new_id, request_id_var
from app.database.db import SessionLocal
from app.security.origin import websocket_origin_ok
from app.security.rate_limit import limiter
from app.settings_store import get_active_provider
from app.voice import factory
from app.voice.session import VoiceSession

logger = logging.getLogger("cypher.api.voice")
router = APIRouter(tags=["voice"])

AUTH_TIMEOUT_SECONDS = 5.0
# Close codes in the 4000-4999 range are free for applications.
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_RATE_LIMIT = 4429


def _active_provider() -> str:
    with SessionLocal() as db:
        return get_active_provider(db)


@router.websocket("/api/voice/ws")
async def voice_ws(ws: WebSocket) -> None:
    settings = get_settings()
    request_id_var.set(new_id())
    client = ws.client.host if ws.client else "unknown"
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in ws.scope.get("headers", [])}

    if not websocket_origin_ok(headers, settings):
        logger.warning("Voice WS rejected: origin %r", headers.get("origin"))
        await ws.close(code=CLOSE_FORBIDDEN_ORIGIN)
        return
    allowed, _ = limiter.allow("voice_connect", client)
    if not allowed or limiter.locked_out(client):
        await ws.close(code=CLOSE_RATE_LIMIT)
        return

    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        token = json.loads(first).get("token", "") if first.startswith("{") else ""
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError, KeyError, RuntimeError):
        token = ""
    if role_for_token(token) != "user":
        limiter.record_auth_failure(client)
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return

    session = VoiceSession(
        ws.send_json,
        ws.send_bytes,
        settings=settings,
        engine_factory=factory.make_engine_factory(settings, _active_provider),
        stt=factory.make_stt(settings),
        tts=factory.make_tts(settings),
        wake=factory.make_wake_word(settings),
        endpointer_factory=lambda: factory.make_endpointer(settings),
    )
    logger.info("Voice session %s opened (client %s, mode %s)", session.session_id, client, session.mode)
    await session.start()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await session.handle_audio(message["bytes"])
            elif message.get("text") is not None:
                await session.handle_text(message["text"])
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        logger.info("Voice session %s closed", session.session_id)
