"""
File tool tests, sandboxed to a fresh tmp_path via ALLOWED_DIRECTORIES.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.tools.file_tools import (
    CopyFileTool,
    DeleteFileTool,
    ListFilesTool,
    MoveFileTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_DIRECTORIES", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def test_write_then_read_file(sandbox):
    write_result = await WriteFileTool().execute(path=str(sandbox / "hello.txt"), content="Hallo Cypher")
    assert write_result.success is True

    read_result = await ReadFileTool().execute(path=str(sandbox / "hello.txt"))
    assert read_result.success is True
    assert read_result.data["content"] == "Hallo Cypher"


async def test_write_file_creates_parent_directories(sandbox):
    target = sandbox / "sub" / "dir" / "file.txt"
    result = await WriteFileTool().execute(path=str(target), content="x")
    assert result.success is True
    assert target.read_text() == "x"


async def test_read_file_outside_allowed_directory_is_refused(sandbox, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("top secret")
    result = await ReadFileTool().execute(path=str(outside))
    assert result.success is False
    assert "outside the allowed directories" in result.error


async def test_read_nonexistent_file(sandbox):
    result = await ReadFileTool().execute(path=str(sandbox / "nope.txt"))
    assert result.success is False


async def test_list_files(sandbox):
    (sandbox / "a.txt").write_text("a")
    (sandbox / "sub").mkdir()
    result = await ListFilesTool().execute(path=str(sandbox))
    assert result.success is True
    names = {e["name"] for e in result.data["entries"]}
    assert names == {"a.txt", "sub"}


async def test_search_files_by_name(sandbox):
    (sandbox / "report_2026.txt").write_text("x")
    (sandbox / "notes.txt").write_text("x")
    result = await SearchFilesTool().execute(query="report", path=str(sandbox))
    assert result.success is True
    assert len(result.data["matches"]) == 1
    assert "report_2026.txt" in result.data["matches"][0]


async def test_move_file(sandbox):
    src = sandbox / "old.txt"
    src.write_text("content")
    dst = sandbox / "new.txt"
    result = await MoveFileTool().execute(source=str(src), destination=str(dst))
    assert result.success is True
    assert not src.exists()
    assert dst.read_text() == "content"


async def test_copy_file(sandbox):
    src = sandbox / "orig.txt"
    src.write_text("content")
    dst = sandbox / "copy.txt"
    result = await CopyFileTool().execute(source=str(src), destination=str(dst))
    assert result.success is True
    assert src.exists()
    assert dst.read_text() == "content"


async def test_delete_file(sandbox):
    target = sandbox / "delete-me.txt"
    target.write_text("x")
    result = await DeleteFileTool().execute(path=str(target))
    assert result.success is True
    assert not target.exists()


async def test_delete_non_empty_directory_is_refused(sandbox):
    d = sandbox / "not-empty"
    d.mkdir()
    (d / "child.txt").write_text("x")
    result = await DeleteFileTool().execute(path=str(d))
    assert result.success is False
    assert d.exists()  # never fell back to a recursive delete


async def test_move_destination_outside_allowed_directory_is_refused(sandbox, tmp_path_factory):
    src = sandbox / "file.txt"
    src.write_text("x")
    outside = tmp_path_factory.mktemp("outside") / "file.txt"
    result = await MoveFileTool().execute(source=str(src), destination=str(outside))
    assert result.success is False
    assert src.exists()  # untouched
