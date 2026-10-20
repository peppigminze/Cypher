"""
Bearer-token authentication.

Two tokens exist:
    user token  - the UI (local PWA, paired phone)
    agent token - the legacy PC-agent process (removed in M5)

Token values come from app/security/tokens.py (.env value, or a token
generated on first start). Every protected endpoint goes through the
dependencies below; repeated failures lock the caller's address out for
a while (app/security/rate_limit.py), which makes guessing the token over
the network impractical.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.security.rate_limit import client_key, limiter, too_many
from app.security.tokens import get_tokens, tokens_match

logger = logging.getLogger("cypher.auth")


def _extract_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'.",
        )
    return parts[1].strip()


def role_for_token(token: str) -> Optional[str]:
    """"user", "agent" or None. Used by HTTP dependencies and WebSockets."""
    tokens = get_tokens(get_settings())
    if token and tokens_match(token, tokens.user_token):
        return "user"
    if token and tokens_match(token, tokens.agent_token):
        return "agent"
    return None


def _check(request: Optional[Request], authorization: str | None, allowed: tuple[str, ...]) -> str:
    client = client_key(request) if request is not None else "unknown"
    locked = limiter.locked_out(client)
    if locked:
        raise too_many(locked, "Zu viele fehlgeschlagene Anmeldeversuche – bitte später erneut versuchen.")
    token = _extract_token(authorization)
    role = role_for_token(token)
    if role not in allowed:
        limiter.record_auth_failure(client)
        logger.warning("Rejected token from %s (expected %s)", client, "/".join(allowed))
        detail = {"user": "Invalid user token.", "agent": "Invalid agent token."}.get(allowed[0], "Invalid token.") if len(allowed) == 1 else "Invalid token."
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    return token


def require_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Dependency for endpoints called by the human user's client (PWA)."""
    return _check(request, authorization, ("user",))


def require_agent(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Dependency for endpoints called only by the local PC agent."""
    return _check(request, authorization, ("agent",))


def require_user_or_agent(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Dependency for endpoints either side may call (e.g. reading tasks)."""
    return _check(request, authorization, ("user", "agent"))


# Kept for callers/tests importing the old helper name.
_tokens_match = tokens_match
