"""
Request-ID middleware (pure ASGI, so it also covers WebSockets and
streaming responses - Starlette's BaseHTTPMiddleware breaks contextvars
propagation for streamed bodies).

Sets `request_id_var` for the whole request, echoes it as `X-Request-ID`,
logs one access line with method, path, status and duration, and records
the duration in the "http" latency metric.
"""
from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import new_id, request_id_var, sanitize_incoming_id
from app.core.metrics import metrics

logger = logging.getLogger("cypher.http")

# Polled endpoints would flood the log at INFO.
_QUIET_PATHS = {"/health", "/api/health", "/api/status"}


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = sanitize_incoming_id(headers.get(b"x-request-id", b"").decode("latin-1") or None)
        request_id = incoming or new_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration = time.perf_counter() - started
            path = scope.get("path", "")
            if scope["type"] == "http":
                metrics.record("http", duration)
                level = logging.DEBUG if path in _QUIET_PATHS and status_code < 400 else logging.INFO
                logger.log(level, "%s %s -> %s (%.0f ms)", scope.get("method"), path, status_code, duration * 1000, extra={"status": status_code, "duration_ms": round(duration * 1000, 1)})
            request_id_var.reset(token)
