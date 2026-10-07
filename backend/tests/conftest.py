from __future__ import annotations

import os
import tempfile
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Make sure "app" is importable when pytest is run from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("USER_TOKEN", "test-user-token")
os.environ.setdefault("AGENT_TOKEN", "test-agent-token")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# Tests must never touch the developer's real Ollama, logs or data, and must
# not spend seconds loading Whisper/Piper/LLM models at app startup.
_TEST_TMP = Path(tempfile.mkdtemp(prefix="cypher-tests-"))
os.environ["APP_ENV"] = "test"
os.environ["LOG_DIR"] = str(_TEST_TMP / "logs")
os.environ["DATA_DIR"] = str(_TEST_TMP / "data")
os.environ["LOCAL_LLM_BASE_URL"] = "http://127.0.0.1:9"  # discard port: connection refused, instantly
os.environ["LLM_WARMUP_ON_START"] = "false"
os.environ["LLM_MONITOR_INTERVAL_SECONDS"] = "0"
os.environ["WHISPER_WARMUP_ON_START"] = "false"
os.environ["TTS_WARMUP_ON_START"] = "false"

from app.database import db as db_module  # noqa: E402
from app.database.db import Base  # noqa: E402
from app.main import app  # noqa: E402

USER_TOKEN = os.environ["USER_TOKEN"]
AGENT_TOKEN = os.environ["AGENT_TOKEN"]


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(test_engine):
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def user_headers():
    return {"Authorization": f"Bearer {USER_TOKEN}"}


@pytest.fixture()
def agent_headers():
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


@pytest.fixture(autouse=True)
def _reset_security_state():
    """Rate-limit buckets, auth lockouts and the token cache are process-wide;
    without a reset, deliberate wrong-token tests would lock out later tests."""
    from app.security import tokens
    from app.security.rate_limit import limiter

    limiter.reset()
    tokens.reset_cache()
    yield
    limiter.reset()
    tokens.reset_cache()
