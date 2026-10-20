from __future__ import annotations

import pytest

from app.database.db import Base, SessionLocal, engine
from app.settings_store import (
    DEFAULT_PROVIDER,
    get_active_provider,
    get_usage_summary,
    record_token_usage,
    set_active_provider,
)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ------------------------------------------------------------ provider toggle


def test_active_provider_defaults_to_local(db_session):
    assert DEFAULT_PROVIDER == "local"
    assert get_active_provider(db_session) == "local"


def test_set_active_provider_persists_and_is_read_back(db_session):
    set_active_provider(db_session, "cloud")
    assert get_active_provider(db_session) == "cloud"


def test_set_active_provider_rejects_unknown_value(db_session):
    with pytest.raises(ValueError):
        set_active_provider(db_session, "quantum")
    # Rejected write must not have silently changed anything.
    assert get_active_provider(db_session) == "local"


def test_active_provider_survives_a_fresh_session(db_session):
    """Simulates a backend restart: a brand-new Session against the same
    engine must still see the persisted choice, not fall back to the
    static .env default."""
    set_active_provider(db_session, "cloud")
    fresh_session = SessionLocal()
    try:
        assert get_active_provider(fresh_session) == "cloud"
    finally:
        fresh_session.close()


def test_set_active_provider_can_switch_back(db_session):
    set_active_provider(db_session, "cloud")
    set_active_provider(db_session, "local")
    assert get_active_provider(db_session) == "local"


# ------------------------------------------------------------ token usage


def test_record_token_usage_accumulates_across_calls(db_session):
    record_token_usage(db_session, "cloud", "gpt-5.6-luna", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    record_token_usage(db_session, "cloud", "gpt-5.6-luna", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

    summary = get_usage_summary(db_session)
    today_total = summary["today"]["total"]
    assert today_total["prompt_tokens"] == 13
    assert today_total["completion_tokens"] == 7
    assert today_total["total_tokens"] == 20
    assert today_total["request_count"] == 2
    assert summary["today"]["by_provider"]["cloud"]["total_tokens"] == 20

    month_total = summary["month"]["total"]
    assert month_total["total_tokens"] == 20


def test_record_token_usage_none_is_a_no_op(db_session):
    record_token_usage(db_session, "local", "qwen2.5:7b", None)
    summary = get_usage_summary(db_session)
    assert summary["today"]["total"]["request_count"] == 0
    assert summary["today"]["by_provider"] == {}


def test_record_token_usage_separates_providers_and_models(db_session):
    record_token_usage(db_session, "local", "qwen2.5:7b", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    record_token_usage(
        db_session, "cloud", "gpt-5.6-luna", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )

    summary = get_usage_summary(db_session)
    by_provider = summary["today"]["by_provider"]
    assert by_provider["local"]["total_tokens"] == 2
    assert by_provider["cloud"]["total_tokens"] == 150
    # request_count and totals must not bleed between providers.
    assert by_provider["local"]["request_count"] == 1
    assert by_provider["cloud"]["request_count"] == 1


def test_record_token_usage_derives_total_when_backend_omits_it(db_session):
    # Some OpenAI-compatible backends may omit "total_tokens" - must not
    # crash, and the derived total should still be correct.
    record_token_usage(db_session, "local", "qwen2.5:7b", {"prompt_tokens": 7, "completion_tokens": 3})
    summary = get_usage_summary(db_session)
    assert summary["today"]["total"]["total_tokens"] == 10
