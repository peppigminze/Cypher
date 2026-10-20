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
  GET /api/security/critical-confirm   "Sicherheitsabfrage für kritische Aktionen"
  PUT /api/security/critical-confirm   switch it - LOCAL UI ONLY (FIX2 3c): not
                          from the phone, not from scripts without a browser,
                          and no LLM tool, voice command or tool result can
                          reach this; every change is written to the audit log
"""
from __future__ import annotations

from typing import Literal, Optional

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.auth import require_user
from app.config import get_settings
from app.core.events import bus
from app.ops import tailscale
from app.security import policy
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
    """Everything the PC's "Handy koppeln" panel shows. The phone opens the
    link (or scans its QR code) once; the token then stays on the phone."""
    _require_local(request)
    settings = get_settings()
    token = get_tokens().user_token
    https_url = tailscale.serve_url(settings.API_PORT)
    links = []
    if https_url:
        links.append({"url": f"{https_url}/#pair={token}", "kind": "tailscale", "microphone": True})
    if not settings.is_localhost_only:
        links.append({"url": f"http://{settings.API_HOST}:{settings.API_PORT}/#pair={token}", "kind": "direct", "microphone": False})
    return {
        "token": token,
        "links": links,
        "qr": _qr_data_uri(links[0]["url"]) if links else None,
        "tailscale": {"installed": tailscale.cli_path() is not None, "url": https_url},
        "serve_command": f"tailscale serve --bg {settings.API_PORT}",
        "note": "Fürs Mikrofon braucht das Handy HTTPS - am einfachsten mit 'tailscale serve' (docs/MOBILE.md).",
    }


def _qr_data_uri(text: str) -> str:
    # Dark modules on white with a quiet zone: phone cameras need the contrast,
    # whatever colour scheme the UI uses.
    return segno.make(text, error="m").svg_data_uri(scale=5, border=4, dark="#000000", light="#ffffff")


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


class CriticalConfirmIn(BaseModel):
    enabled: bool
    scope: Literal["session", "permanent"] = "session"


_LOCAL_UI_ONLY = (
    "Die Sicherheitsabfrage lässt sich nur direkt am PC in der Cypher-Oberfläche ändern "
    "(nicht vom Handy, nicht per Sprache und nicht durch Cypher selbst)."
)


@router.get("/api/security/critical-confirm")
def get_critical_confirm(_user=Depends(require_user)) -> dict:
    return policy.confirm_state()


@router.put("/api/security/critical-confirm", dependencies=[Depends(rate_limit("auth_session"))])
async def put_critical_confirm(payload: CriticalConfirmIn, request: Request, _user=Depends(require_user)) -> dict:
    peer = request.client.host if request.client else None
    # Stricter than _require_local: the browser MUST say it is a same-origin
    # fetch (page scripts cannot forge Sec-Fetch-Site; scripts without a
    # browser simply don't send it).
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site not in ("same-origin", "same-site") or not is_trusted_local_browser_request(peer, request.headers, get_settings()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_LOCAL_UI_ONLY)
    state = await policy.set_critical_confirm(payload.enabled, payload.scope, actor="ui")
    bus.publish("security.critical_confirm", **state)
    return state

