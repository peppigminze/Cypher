"""
POST /api/system/shutdown - let stop_cypher.bat end Cypher cleanly.

Killing the process would skip the shutdown steps (pending conversation
writes, closing the model clients, the final log line). This endpoint asks
the uvicorn server started by run.py to exit, which runs the normal
lifespan shutdown. Only from this PC (loopback) and only with the token.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_user
from app.security.origin import is_direct_local_request

logger = logging.getLogger("cypher.api.system")
router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/shutdown")
async def shutdown(request: Request, _user=Depends(require_user)) -> dict:
    peer = request.client.host if request.client else None
    if not is_direct_local_request(peer, request.headers):
        raise HTTPException(status_code=403, detail="Beenden geht nur direkt auf dem PC (stop_cypher.bat).")
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=409, detail="Nicht über run.py gestartet - bitte das Fenster schliessen bzw. den Prozess beenden.")
    logger.info("Shutdown requested via /api/system/shutdown")
    server.should_exit = True
    return {"stopping": True}
