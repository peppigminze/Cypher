"""
Auth bootstrap, audit log, pending approvals and tool catalog endpoints.

  GET /api/auth/session   token for the LOCAL UI only (same-site request from
                          this PC; see app/security/origin.py) - the user never
                          types a token on the PC itself
  GET /api/auth/pairing   pairing link for other devices (local UI only)
  GET /api/auth/check     "is my stored token still valid?"
  GET /api/audit          recent tool decisions
  GET /api/approvals      approvals currently waiting for the user
  GET /api/tools          every tool with risk tier and whether the LLM sees it
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import require_user
from app.config import get_settings
from app.security.origin import is_trusted_local_browser_request
from app.security.rate_limit import rate_limit
from app.security.tokens import get_tokens
from app.tools import pending_approvals
from app.tools.audit import get_audit_log
from app.tools.catalog import describe_catalog

router = APIRouter(tags=["security"])

_LOCAL_ONLY = (
    "Nur direkt am PC verfügbar. Auf anderen Geräten (z. B. Handy) einmalig den Kopplungscode "
    "aus der Cypher-Oberfläche am PC eingeben."
)


def _require_local(request: Request) -> None:
    peer = request.client.host if request.client else None
    if not is_trusted_local_browser_request(peer, request.headers, get_settings()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_LOCAL_ONLY)


@router.get("/api/auth/session", dependencies=[Depends(rate_limit("auth_session"))])
def auth_session(request: Request) -> dict:
    _require_local(request)
    tokens = get_tokens()
    return {"token": tokens.user_token, "token_source": tokens.user_source}


@router.get("/api/auth/pairing", dependencies=[Depends(rate_limit("auth_session"))])
def auth_pairing(request: Request) -> dict:
    _require_local(request)
    settings = get_settings()
    token = get_tokens().user_token
    hosts = [] if settings.is_localhost_only else [settings.API_HOST]
    return {
        "token": token,
        "links": [f"http://{h}:{settings.API_PORT}/#pair={token}" for h in hosts],
        "note": "Für Mikrofonzugriff auf dem Handy wird HTTPS benötigt (siehe docs/MOBILE.md).",
    }


@router.get("/api/auth/check")
def auth_check(_user=Depends(require_user)) -> dict:
    return {"ok": True}


@router.get("/api/audit")
def audit_log(
    _user=Depends(require_user),
    limit: int = Query(100, ge=1, le=1000),
    tool: Optional[str] = Query(None, max_length=128),
) -> dict:
    return {"entries": get_audit_log().recent(limit=limit, tool=tool)}


@router.get("/api/approvals")
def list_approvals(_user=Depends(require_user)) -> dict:
    return {"pending": [p.public() for p in pending_approvals.list_pending()]}


@router.get("/api/tools")
def list_tools(_user=Depends(require_user)) -> dict:
    return {"tools": describe_catalog()}
