from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import actions
from app.api import agent as agent_api
from app.api import (
    health,
    memory,
    messages,
    projects,
    settings as settings_api,
    speak,
    sync,
    tasks,
    tool_chat,
    transcribe,
    usage,
)
from app.config import get_settings
from app.core.errors import install_exception_handlers
from app.core.health import APP_VERSION
from app.core.log import setup_logging
from app.core.middleware import RequestIdMiddleware
from app.database.db import init_db
from app.runtime import Runtime, set_runtime
from app.scheduler import run_forever as run_scheduler_forever

logger = logging.getLogger("cypher.backend")

settings = get_settings()


_PLACEHOLDER_TOKENS = {"change-me-user-token", "change-me-agent-token"}


def _check_tokens(settings) -> None:
    """.env.example ships obviously-fake placeholder tokens.

    Reachable only from localhost, a placeholder token is low-stakes (an
    attacker already needs code execution on this PC). Bound beyond
    localhost (Tailscale, LAN - see TAILSCALE.md) it is a real credential
    anyone who has seen this public repo could use, so that combination
    refuses to start instead of logging an easy-to-miss warning.

    Only sees the real bind address when started via run.py (which reads
    API_HOST); a hand-typed `uvicorn --host ...` bypasses this check.
    """
    has_placeholder = settings.USER_TOKEN in _PLACEHOLDER_TOKENS or settings.AGENT_TOKEN in _PLACEHOLDER_TOKENS
    if not has_placeholder:
        return

    exposed_beyond_localhost = settings.API_HOST not in ("127.0.0.1", "localhost", "::1")
    if exposed_beyond_localhost:
        raise RuntimeError(
            f"Refusing to start: API_HOST='{settings.API_HOST}' is reachable beyond localhost "
            "(see TAILSCALE.md), but USER_TOKEN/AGENT_TOKEN are still the placeholder values "
            "from .env.example. Set your own random tokens in .env before binding beyond "
            "localhost."
        )

    logger.warning(
        "SECURITY: USER_TOKEN and/or AGENT_TOKEN is still the placeholder value from "
        ".env.example. Anyone who has seen that file can authenticate. Set your own "
        "random tokens in .env before exposing this backend beyond localhost."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_file = setup_logging(settings)
    logger.info("Cypher backend %s starting (env=%s, log=%s)", APP_VERSION, settings.APP_ENV, log_file)
    _check_tokens(settings)
    init_db()
    logger.info("Database ready.")

    runtime = Runtime(settings)
    set_runtime(runtime)
    await runtime.start()
    scheduler_task = asyncio.create_task(run_scheduler_forever(), name="reminder-scheduler")
    try:
        yield
    finally:
        logger.info("Cypher backend shutting down.")
        scheduler_task.cancel()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
            pass
        await runtime.stop()
        set_runtime(None)


app = FastAPI(title="Cypher Backend", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Audio-Duration-Seconds", "X-Generation-Time-Seconds", "X-Sample-Rate", "X-Request-ID"],
)
# Added last = outermost: the request ID exists before CORS and every handler run.
app.add_middleware(RequestIdMiddleware)
install_exception_handlers(app)

app.include_router(health.router)
app.include_router(messages.router)
app.include_router(tasks.router)
app.include_router(memory.router)
app.include_router(sync.router)
app.include_router(agent_api.router)
app.include_router(actions.router)
app.include_router(settings_api.router)
app.include_router(projects.router)
app.include_router(tool_chat.router)
app.include_router(transcribe.router)
app.include_router(speak.router)
app.include_router(usage.router)


@app.get("/")
def root() -> dict:
    return {"name": "Cypher Backend", "status": "running", "version": APP_VERSION}
