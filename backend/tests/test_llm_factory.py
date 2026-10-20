"""Provider factory: local by default, cloud only when switched on, keyed and within budget."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.core import errors
from app.llm import factory
from app.llm.cloud_provider import CloudBudgetExceeded, CloudLLMProvider, CloudNotConfigured
from app.llm.factory import cloud_status, get_provider_by_name
from app.llm.ollama import OllamaProvider


def settings(**update):
    return get_settings().model_copy(update=update)


def test_local_provider_from_config():
    provider = get_provider_by_name("local", settings(LOCAL_LLM_MODEL="qwen3.5:4b"))
    assert isinstance(provider, OllamaProvider) and provider.model == "qwen3.5:4b"


def test_cloud_needs_the_master_switch_and_a_key():
    assert cloud_status(settings(CLOUD_LLM_ENABLED=False, CLOUD_LLM_API_KEY="sk-x")) == "disabled"
    assert cloud_status(settings(CLOUD_LLM_ENABLED=True, CLOUD_LLM_API_KEY="")) == "no_key"
    with pytest.raises(CloudNotConfigured) as exc:
        get_provider_by_name("cloud", settings(CLOUD_LLM_ENABLED=True, CLOUD_LLM_API_KEY=""))
    assert errors.from_exception(exc.value).code == "cloud_not_configured"


def test_cloud_provider_from_config(monkeypatch):
    monkeypatch.setattr(factory, "monthly_cloud_spend_usd", lambda: 0.0)
    provider = get_provider_by_name("cloud", settings(CLOUD_LLM_ENABLED=True, CLOUD_LLM_API_KEY="sk-test", CLOUD_LLM_MODEL="gpt-6-luna", CLOUD_LLM_REASONING_EFFORT="none"))
    assert isinstance(provider, CloudLLMProvider) and provider.model == "gpt-6-luna" and provider.reasoning_effort == "none"


def test_cloud_refused_when_monthly_budget_is_used_up(monkeypatch):
    monkeypatch.setattr(factory, "monthly_cloud_spend_usd", lambda: 5.01)
    with pytest.raises(CloudBudgetExceeded) as exc:
        get_provider_by_name("cloud", settings(CLOUD_LLM_ENABLED=True, CLOUD_LLM_API_KEY="sk-test", CLOUD_MONTHLY_BUDGET_USD=5.0))
    err = errors.from_exception(exc.value)
    assert err.code == "cloud_budget_exceeded" and "5.01 von 5.00 USD" in err.message


def test_budget_zero_means_no_cap(monkeypatch):
    monkeypatch.setattr(factory, "monthly_cloud_spend_usd", lambda: 999.0)
    assert get_provider_by_name("cloud", settings(CLOUD_LLM_ENABLED=True, CLOUD_LLM_API_KEY="sk-test", CLOUD_MONTHLY_BUDGET_USD=0))


def test_unknown_provider():
    with pytest.raises(ValueError):
        get_provider_by_name("quantum")
