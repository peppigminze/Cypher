"""
In-memory rate limiting (token bucket per client + bucket name).

Single-process, single-user app: no Redis needed. Limits are generous for
a human, but stop scripted abuse - most importantly brute-forcing the
token once the API is reachable over Tailscale. Repeated auth failures
from one address lock that address out for a while.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple

from fastapi import HTTPException, Request, status

from app.security.origin import is_loopback


@dataclass(frozen=True)
class Limit:
    capacity: int  # burst size
    per_seconds: float  # time to refill the full bucket


# Bucket name -> limit. Tuned for one human talking to Cypher.
LIMITS: Dict[str, Limit] = {
    "chat": Limit(30, 60),  # LLM requests (tool-chat, text chat)
    "audio": Limit(60, 60),  # /transcribe, /speak
    "approval": Limit(30, 60),
    "auth_session": Limit(20, 60),
    "voice_connect": Limit(20, 60),
    "default": Limit(300, 60),
}

AUTH_FAILURE_LIMIT = 10  # failed token checks ...
AUTH_FAILURE_WINDOW = 300.0  # ... within 5 minutes ...
AUTH_LOCKOUT_SECONDS = 300.0  # ... lock the address out for 5 minutes


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._failures: Dict[str, list[float]] = {}
        self._locked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str, client: str, now: float | None = None) -> Tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        limit = LIMITS.get(bucket, LIMITS["default"])
        now = time.monotonic() if now is None else now
        rate = limit.capacity / limit.per_seconds
        with self._lock:
            tokens, last = self._buckets.get((bucket, client), (float(limit.capacity), now))
            tokens = min(float(limit.capacity), tokens + (now - last) * rate)
            if tokens >= 1.0:
                self._buckets[(bucket, client)] = (tokens - 1.0, now)
                return True, 0.0
            self._buckets[(bucket, client)] = (tokens, now)
            return False, (1.0 - tokens) / rate

    def record_auth_failure(self, client: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < AUTH_FAILURE_WINDOW]
            recent.append(now)
            self._failures[client] = recent
            if len(recent) >= AUTH_FAILURE_LIMIT:
                self._locked_until[client] = now + AUTH_LOCKOUT_SECONDS
                self._failures[client] = []

    def locked_out(self, client: str, now: float | None = None) -> float:
        """Seconds the client is still locked out (0 = not locked)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._locked_until.get(client, 0.0)
            return max(0.0, until - now)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._failures.clear()
            self._locked_until.clear()


limiter = RateLimiter()


def client_key(request: Request) -> str:
    """Who to count against. Behind `tailscale serve` every request arrives
    from 127.0.0.1, so the real device comes from X-Forwarded-For - trusted
    only from loopback (just a program on this PC can set it there).
    Without this, wrong tokens from a tailnet device would lock out the PC."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and is_loopback(peer):
        return "fwd:" + forwarded.split(",")[0].strip()
    return peer


def too_many(retry_after: float, message: str = "Zu viele Anfragen – kurz warten und nochmal versuchen.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=message,
        headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
    )


def rate_limit(bucket: str):
    """FastAPI dependency factory: `Depends(rate_limit("chat"))`."""

    def _dependency(request: Request) -> None:
        allowed, retry_after = limiter.allow(bucket, client_key(request))
        if not allowed:
            raise too_many(retry_after)

    return _dependency
