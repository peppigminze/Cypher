from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import actions
from app.api import agent as agent_api
from app.api import health, memory, messages, projects, settings as settings_api, speak, sync, tasks, tool_chat, transcribe
from app.config import get_settings
from app.database.db import init_db
from app.scheduler import run_forever as run_scheduler_forever

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cypher.backend")

settings = get_settings()


_PLACEHOLDER_TOKENS = {"change-me-user-token", "change-me-agent-token"}


def _warn_if_default_tokens(settings) -> None:
    """.env.example ships obviously-fake placeholder tokens with an
    explicit "do not use these" comment - but nothing stopped someone
    from starting the backend without changing them, silently running
    with a publicly-known credential. Loud, not blocking: V1 has no
    other bootstrap step to hang a hard failure on, and a warning still
    gets the point across every single startup until it's fixed."""
    if settings.USER_TOKEN in _PLACEHOLDER_TOKENS or settings.AGENT_TOKEN in _PLACEHOLDER_TOKENS:
        logger.warning(
            "SECURITY: USER_TOKEN and/or AGENT_TOKEN is still the placeholder value from "
            ".env.example. Anyone who has seen that file can authenticate. Set your own "
            "random tokens in .env before exposing this backend beyond localhost."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Cypher backend starting (env=%s)", settings.APP_ENV)
    _warn_if_default_tokens(settings)
    init_db()
    logger.info("Database ready.")
    scheduler_task = asyncio.create_task(run_scheduler_forever())
    yield
    scheduler_task.cancel()


app = FastAPI(title="Cypher Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this, the browser hides custom response headers from
    # fetch() even on a successful cross-origin request - needed so the
    # /speak test page can read the timing headers (see app/api/speak.py).
    expose_headers=["X-Audio-Duration-Seconds", "X-Generation-Time-Seconds", "X-Sample-Rate"],
)

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


@app.get("/")
def root() -> dict:
    return {"name": "Cypher Backend", "status": "running"}
