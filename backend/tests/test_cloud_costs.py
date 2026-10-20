"""Cloud cost counter: every cloud call is priced and summed per day/month (M5)."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.settings_store import get_usage_summary, month_cost_usd, record_token_usage


@pytest.fixture()
def db(test_engine):
    session = sessionmaker(bind=test_engine)()
    yield session
    session.close()


def test_cloud_calls_are_priced_and_local_is_free(db):
    record_token_usage(db, "cloud", "gpt-6-luna", {"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "cached_prompt_tokens": 400_000})
    record_token_usage(db, "local", "qwen3.5:9b", {"prompt_tokens": 5000, "completion_tokens": 500})
    summary = get_usage_summary(db)
    assert summary["today"]["by_provider"]["cloud"]["cost_usd"] == pytest.approx(0.114)
    assert summary["today"]["by_provider"]["local"]["cost_usd"] == 0
    assert month_cost_usd(db) == pytest.approx(0.114)


def test_usage_api_reports_cost_and_budget(client, user_headers):
    data = client.get("/api/usage", headers=user_headers).json()
    assert data["today"]["total"]["cost_usd"] == 0
    from app.config import get_settings

    assert data["cloud_monthly_budget_usd"] == get_settings().CLOUD_MONTHLY_BUDGET_USD
    assert data["cloud_model"] == get_settings().CLOUD_LLM_MODEL
