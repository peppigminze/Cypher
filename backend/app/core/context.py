"""
Per-request / per-pipeline correlation IDs.

`contextvars` (not thread-locals) because FastAPI runs request handlers
as asyncio tasks: each task gets its own copy of the context, so two
concurrent requests never see each other's IDs. The logging filter in
app/core/log.py reads these and stamps every log line with them, which
makes it possible to follow one voice command through STT -> LLM -> tool
-> TTS in the log file.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
pipeline_id_var: ContextVar[str] = ContextVar("pipeline_id", default="-")

# Client-supplied IDs are echoed into logs, so only accept a harmless shape.
_VALID_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def sanitize_incoming_id(raw: str | None) -> str | None:
    if raw and _VALID_ID.match(raw):
        return raw
    return None


def current_request_id() -> str:
    return request_id_var.get()


def current_pipeline_id() -> str:
    return pipeline_id_var.get()
