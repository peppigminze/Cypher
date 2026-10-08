"""
SQLAlchemy engine / session setup.

V1 deliberately uses the *synchronous* SQLAlchemy API with SQLite.
This keeps the code simple and predictable (see project rule:
no over-engineering in V1). SQLite + sync sessions are more than
enough for a single-user local-first assistant.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

# backend/app/database/db.py -> backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

_SQLITE_PREFIX = "sqlite:///"


def _resolve_database_url(raw_url: str) -> str:
    """Resolve a relative sqlite file path against backend/, not the
    process's current working directory.

    DATABASE_URL is documented (see .env.example) as "relative to
    backend/ when the backend is started there" - but sqlite resolves
    relative paths against the CWD of whichever process opened the
    connection. Since the local PC agent (agent/run_agent.py) is
    started from the project root while the backend is started from
    backend/, an unresolved relative path made each process silently
    talk to a *different* database file (backend/jarvis.db vs.
    ./jarvis.db), so tasks/memories saved by agent tool calls never
    showed up in the backend's API responses. In-memory URLs
    (":memory:") and absolute/non-sqlite URLs are left untouched.
    """
    if not raw_url.startswith(_SQLITE_PREFIX):
        return raw_url

    path_part = raw_url[len(_SQLITE_PREFIX):]
    if path_part == ":memory:" or path_part.startswith("file:"):
        return raw_url

    path = Path(path_part)
    if path.is_absolute():
        return raw_url

    resolved = (_BACKEND_DIR / path).resolve()
    return f"{_SQLITE_PREFIX}{resolved.as_posix()}"


settings = get_settings()

connect_args = {}
engine_kwargs: dict = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # allow usage across the FastAPI thread pool
    connect_args = {"check_same_thread": False}
    if ":memory:" in settings.DATABASE_URL:
        # One shared connection, otherwise every worker thread (audit log,
        # scheduler) would see its own empty in-memory database.
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(_resolve_database_url(settings.DATABASE_URL), connect_args=connect_args, future=True, **engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def ensure_columns(table_name: str, column_defs: dict[str, str]) -> None:
    """Add any columns in `column_defs` (name -> full "TYPE ..." DDL,
    e.g. "TEXT NOT NULL DEFAULT 'fact'") that don't already exist on
    `table_name`. No-op for columns that are already there.

    There is no Alembic in V1 (see project spec section 32: "vermeide
    unnötige Migrationen ohne Grund"), but a bare `Base.metadata.create_all()`
    only creates missing *tables* - it silently does nothing for a new
    column on a table that already exists, which would crash the first
    request that touches it. This is the minimal safe alternative:
    additive, idempotent, and never touches or drops existing data.

    SECURITY: `table_name`/`column_defs` are interpolated directly into
    SQL (SQLite's DDL statements don't support parameterized identifiers
    the way DML does). This is safe ONLY because every call site is a
    hardcoded literal in run_migrations() below - never call this with
    a table/column name derived from a request, config value, or any
    other untrusted input.
    """
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
        for column, ddl in column_defs.items():
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}"))
        conn.commit()


def init_db() -> None:
    """Create all tables and apply any additive column migrations.
    Safe to call multiple times."""
    from app.database import models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)
    models.run_migrations()
    from app.memory.store import ensure_fts  # noqa: PLC0415

    ensure_fts(engine)  # full-text index for the memory archive (M4)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for use outside of FastAPI (e.g. the agent worker,
    tests, or scripts)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
