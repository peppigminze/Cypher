"""
Tests for the retry-vs-give-up decision in agent/sync_worker.py
(project spec section 19). The agent/ package lives at the project
root, sibling to backend/ - not on pytest's default path, so it's
added here the same way agent/run_agent.py does it for itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.sync_worker import SyncWorker  # noqa: E402


class FakeClient:
    def __init__(self):
        self.retried: list[tuple] = []
        self.failed: list[tuple] = []

    async def retry_message(self, message_id, retry_count, next_retry_at, error):
        self.retried.append((message_id, retry_count, next_retry_at, error))

    async def fail_message(self, message_id, error):
        self.failed.append((message_id, error))


@pytest.fixture()
def worker(monkeypatch):
    # SyncWorker.__init__ builds a real CypherAgent (LLM provider + tool
    # registry) which we don't need for this pure retry-decision test.
    monkeypatch.setattr(SyncWorker, "__init__", lambda self, client: setattr(self, "client", client))
    return SyncWorker(client=FakeClient())


async def test_retries_under_the_limit(worker):
    await worker._retry_or_fail(message_id=1, retry_count=0, error="LLM down")
    assert len(worker.client.retried) == 1
    assert worker.client.failed == []
    message_id, retry_count, _next_retry_at, error = worker.client.retried[0]
    assert message_id == 1
    assert retry_count == 1
    assert error == "LLM down"


async def test_gives_up_after_max_retries(monkeypatch, worker):
    from app.config import get_settings

    monkeypatch.setenv("MESSAGE_MAX_RETRIES", "3")
    get_settings.cache_clear()
    try:
        await worker._retry_or_fail(message_id=2, retry_count=3, error="LLM down")
    finally:
        get_settings.cache_clear()

    assert worker.client.retried == []
    assert len(worker.client.failed) == 1
    assert worker.client.failed[0][0] == 2
    assert "3 Versuchen" in worker.client.failed[0][1]


async def test_backoff_increases_with_retry_count(monkeypatch):
    """Later retries should be scheduled further in the future, not
    hammered at a fixed interval."""
    from datetime import datetime, timezone

    monkeypatch.setattr(SyncWorker, "__init__", lambda self, client: setattr(self, "client", client))
    worker = SyncWorker(client=FakeClient())

    before = datetime.now(timezone.utc)
    await worker._retry_or_fail(message_id=1, retry_count=0, error="x")
    await worker._retry_or_fail(message_id=1, retry_count=1, error="x")

    first_next_retry = datetime.fromisoformat(worker.client.retried[0][2])
    second_next_retry = datetime.fromisoformat(worker.client.retried[1][2])
    assert (second_next_retry - before) > (first_next_retry - before)
