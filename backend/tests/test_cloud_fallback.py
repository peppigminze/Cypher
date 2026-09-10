"""
Tests for the optional cloud LLM fallback (project spec sections 8/9).
Uses fake providers - no real network/API key needed. There is no live
test against a real cloud API in this project since that would require
the user's own API key; see README.md section 17 for that caveat.
"""
from __future__ import annotations

import pytest

from app.agent.core import CypherAgent
from app.config import get_settings
from app.database.db import Base, SessionLocal, engine
from app.llm.base import LLMProvider, LLMUnavailableError
from app.llm.factory import get_cloud_llm_provider
from app.tools.defaults import build_default_registry


class FakeLocalLLM(LLMProvider):
    def __init__(self, available: bool = True):
        self.available = available
        self.calls = 0

    async def chat(self, messages, temperature: float = 0.3) -> str:
        self.calls += 1
        if not self.available:
            raise LLMUnavailableError("local down")
        return '{"tool": null, "arguments": {}, "done": true, "reply": "local reply"}'

    async def health_check(self) -> bool:
        return self.available


class FakeCloudLLM(LLMProvider):
    def __init__(self, available: bool = True):
        self.available = available
        self.calls = 0

    async def chat(self, messages, temperature: float = 0.3) -> str:
        self.calls += 1
        if not self.available:
            raise LLMUnavailableError("cloud down")
        return '{"tool": null, "arguments": {}, "done": true, "reply": "cloud reply"}'

    async def health_check(self) -> bool:
        return self.available


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


async def test_uses_local_when_available_never_touches_cloud(db_session):
    local = FakeLocalLLM(available=True)
    cloud = FakeCloudLLM(available=True)
    agent = CypherAgent(llm=local, tools=build_default_registry(), cloud_llm=cloud)

    result = await agent.run_pipeline(db_session, "hi")

    assert result.reply == "local reply"
    assert cloud.calls == 0  # cost control: cloud never touched when local works
    assert result.processed_by == "local"


async def test_falls_back_to_cloud_when_local_unavailable(db_session):
    local = FakeLocalLLM(available=False)
    cloud = FakeCloudLLM(available=True)
    agent = CypherAgent(llm=local, tools=build_default_registry(), cloud_llm=cloud)

    result = await agent.run_pipeline(db_session, "hi")

    assert result.reply == "cloud reply"
    assert local.calls == 1
    assert result.processed_by == "cloud"


async def test_processed_by_resets_between_independent_messages(db_session):
    """A later message must not inherit a stale processed_by=cloud from
    an earlier one just because they share the same CypherAgent
    instance (which sync_worker does - one instance for the process's
    whole lifetime)."""
    local_down_then_up = FakeLocalLLM(available=False)
    cloud = FakeCloudLLM(available=True)
    agent = CypherAgent(llm=local_down_then_up, tools=build_default_registry(), cloud_llm=cloud)

    first = await agent.run_pipeline(db_session, "first message")
    assert first.processed_by == "cloud"

    local_down_then_up.available = True
    second = await agent.run_pipeline(db_session, "second message")
    assert second.processed_by == "local"


async def test_no_cloud_configured_still_raises_llm_unavailable(db_session):
    local = FakeLocalLLM(available=False)
    agent = CypherAgent(llm=local, tools=build_default_registry(), cloud_llm=None)

    with pytest.raises(LLMUnavailableError):
        await agent.run_pipeline(db_session, "hi")


async def test_both_local_and_cloud_unavailable_raises(db_session):
    local = FakeLocalLLM(available=False)
    cloud = FakeCloudLLM(available=False)
    agent = CypherAgent(llm=local, tools=build_default_registry(), cloud_llm=cloud)

    with pytest.raises(LLMUnavailableError):
        await agent.run_pipeline(db_session, "hi")


# ---------------------------------------------------------------- factory (cost control defaults)


def test_cloud_provider_is_none_when_disabled(monkeypatch):
    monkeypatch.setenv("CLOUD_LLM_ENABLED", "false")
    monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-something")
    get_settings.cache_clear()
    try:
        assert get_cloud_llm_provider() is None
    finally:
        get_settings.cache_clear()


def test_cloud_provider_is_none_without_api_key(monkeypatch):
    monkeypatch.setenv("CLOUD_LLM_ENABLED", "true")
    monkeypatch.setenv("CLOUD_LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert get_cloud_llm_provider() is None
    finally:
        get_settings.cache_clear()


def test_cloud_provider_is_built_when_enabled_with_key(monkeypatch):
    monkeypatch.setenv("CLOUD_LLM_ENABLED", "true")
    monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-something")
    get_settings.cache_clear()
    try:
        provider = get_cloud_llm_provider()
        assert provider is not None
    finally:
        get_settings.cache_clear()


def test_default_is_disabled_out_of_the_box(monkeypatch):
    """The out-of-the-box default (no explicit .env override) must be
    disabled - zero cloud cost unless the user opts in."""
    monkeypatch.delenv("CLOUD_LLM_ENABLED", raising=False)
    monkeypatch.delenv("CLOUD_LLM_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().CLOUD_LLM_ENABLED is False
    finally:
        get_settings.cache_clear()
