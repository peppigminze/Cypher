from __future__ import annotations

import pytest

from app.config import get_settings
from app.llm.base import LLMUnavailableError
from app.llm.cloud_provider import CloudLLMProvider
from app.llm.factory import get_provider_by_name
from app.llm.local_provider import LocalLLMProvider


@pytest.fixture()
def settings_env(monkeypatch):
    """Same pattern as test_file_tools.py's `sandbox` fixture: set env
    vars via monkeypatch, clear the get_settings() cache so they take
    effect, and clear it again on teardown so later tests aren't
    affected."""
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_get_provider_by_name_local_returns_local_provider(settings_env):
    settings_env.setenv("LOCAL_LLM_MODEL", "qwen2.5:7b")
    get_settings.cache_clear()

    provider = get_provider_by_name("local", get_settings())

    assert isinstance(provider, LocalLLMProvider)
    assert provider.model == "qwen2.5:7b"


def test_get_provider_by_name_cloud_returns_cloud_provider_when_key_set(settings_env):
    settings_env.setenv("CLOUD_LLM_API_KEY", "sk-test-key")
    settings_env.setenv("CLOUD_LLM_MODEL", "gpt-5.6-luna")
    get_settings.cache_clear()

    provider = get_provider_by_name("cloud", get_settings())

    assert isinstance(provider, CloudLLMProvider)
    assert provider.model == "gpt-5.6-luna"


def test_get_provider_by_name_cloud_without_key_raises_clearly(settings_env):
    settings_env.setenv("CLOUD_LLM_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(LLMUnavailableError) as exc_info:
        get_provider_by_name("cloud", get_settings())
    assert "CLOUD_LLM_API_KEY" in str(exc_info.value)


def test_get_provider_by_name_unknown_provider_raises_value_error(settings_env):
    with pytest.raises(ValueError):
        get_provider_by_name("quantum", get_settings())
