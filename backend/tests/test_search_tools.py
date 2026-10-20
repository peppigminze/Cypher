from __future__ import annotations

import pytest

from app.config import get_settings
from app.tools.search_tools import FindFilesTool


@pytest.fixture()
def search_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_DIRECTORIES", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def test_find_files_matches_by_name(search_root):
    (search_root / "quarterly_report.txt").write_text("x")
    (search_root / "notes.txt").write_text("x")

    result = await FindFilesTool().execute(query="report")

    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["results"][0]["filename"] == "quarterly_report.txt"
    assert result.data["results"][0]["modified"] is not None


async def test_find_files_no_matches_is_a_successful_empty_result(search_root):
    (search_root / "notes.txt").write_text("x")

    result = await FindFilesTool().execute(query="nonexistent-xyz")

    assert result.success is True
    assert result.data["count"] == 0
    assert result.data["results"] == []


async def test_find_files_respects_max_results(search_root):
    for i in range(5):
        (search_root / f"match_{i}.txt").write_text("x")

    result = await FindFilesTool().execute(query="match", max_results=2)

    assert result.success is True
    assert result.data["count"] == 2


async def test_find_files_caps_max_results_at_the_configured_ceiling(search_root, monkeypatch):
    monkeypatch.setenv("SEARCH_MAX_RESULTS", "1")
    get_settings.cache_clear()
    for i in range(3):
        (search_root / f"match_{i}.txt").write_text("x")

    result = await FindFilesTool().execute(query="match", max_results=100)

    assert result.success is True
    assert result.data["count"] == 1


async def test_find_files_empty_query_is_rejected(search_root):
    result = await FindFilesTool().execute(query="   ")
    assert result.success is False
    assert "must not be empty" in result.error


async def test_find_files_non_numeric_max_results_is_rejected(search_root):
    result = await FindFilesTool().execute(query="notes", max_results="lots")  # type: ignore[arg-type]
    assert result.success is False
    assert "max_results" in result.error


async def test_find_files_missing_search_directory_warns_but_does_not_fail(monkeypatch):
    monkeypatch.setenv("SEARCH_DIRECTORIES", "Z:\\this-should-not-exist\\anywhere")
    get_settings.cache_clear()
    try:
        result = await FindFilesTool().execute(query="anything")
    finally:
        get_settings.cache_clear()

    assert result.success is True
    assert result.data["count"] == 0
    assert result.data["warnings"] is not None
    assert "does not exist" in result.data["warnings"][0]
