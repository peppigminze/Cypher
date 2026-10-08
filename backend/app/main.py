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
    security_api,
    settings as settings_api,
    speak,
    sync,
    tasks,
    tool_chat,
    transcribe,
    usage,
    voice_ws,
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
    """Startup safety check for the configured tokens.

    Placeholder values from .env.example are never accepted as credentials
    any more - app/security/tokens.py generates a random token instead - so
    they are only reported. What remains dangerous is a *weak* token (short
    value typed into .env) while the API is reachable beyond this PC
    (Tailscale/LAN, see TAILSCALE.md): that combination refuses to start.

    Only sees the real bind address when started via run.py (which reads
    API_HOST); a hand-typed `uvicorn --host ...` bypasses this check.
    """
    from app.security.tokens import is_weak  # noqa: PLC0415

    exposed = settings.API_HOST not in ("127.0.0.1", "localhost", "::1")
    for name in ("USER_TOKEN", "AGENT_TOKEN"):
        value = getattr(settings, name)
        if value in _PLACEHOLDER_TOKENS or not value:
            logger.info("%s is a placeholder in .env - using the generated token from the data directory instead.", name)
            continue
        if is_weak(value):
            if exposed:
                raise RuntimeError(
                    f"Refusing to start: API_HOST='{settings.API_HOST}' is reachable beyond localhost, "
                    f"but {name} is shorter than 24 characters. Use a long random token "
                    "(or remove it from .env to get a generated one)."
                )
            logger.warning("SECURITY: %s is short (weak). Fine for localhost-only use, not for remote access.", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_file = setup_logging(settings)
    logger.info("Cypher backend %s starting (env=%s, log=%s)", APP_VERSION, settings.APP_ENV, log_file)
    _check_tokens(settings)
    init_db()
    logger.info("Database ready.")
    from app.agent.engine import conversations  # noqa: PLC0415

    from app.memory.budget import context_budget  # noqa: PLC0415

    budget = context_budget(settings)
    if not budget["fits"]:
        logger.warning(
            "Context budget %d tokens > LOCAL_LLM_NUM_CTX %d: long turns may lose the start of the prompt. "
            "Lower HISTORY_MAX_TOKENS / MEMORY_* / FETCH_MAX_CHARS or raise LOCAL_LLM_NUM_CTX. Parts: %s",
            budget["total"], budget["window"], budget["parts"],
        )
    pruned = conversations.prune(settings.CONVERSATION_KEEP_DAYS)
    if pruned:
        logger.info("Deleted %d conversation histories older than %d days.", pruned, settings.CONVERSATION_KEEP_DAYS)

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
        conversations.flush()


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
app.include_router(memory.conversations_router)
app.include_router(sync.router)
app.include_router(agent_api.router)
app.include_router(actions.router)
app.include_router(settings_api.router)
app.include_router(projects.router)
app.include_router(tool_chat.router)
app.include_router(transcribe.router)
app.include_router(speak.router)
app.include_router(usage.router)
app.include_router(security_api.router)
app.include_router(voice_ws.router)


@app.get("/")
def root() -> dict:
    return {"name": "Cypher Backend", "status": "running", "version": APP_VERSION}
