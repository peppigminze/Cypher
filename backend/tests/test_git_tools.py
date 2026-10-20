"""
Git tool tests, sandboxed to a fresh tmp_path via ALLOWED_DIRECTORIES,
using real git repos (git must be on PATH - already a project
dependency for development).
"""
from __future__ import annotations

import subprocess

import pytest

from app.config import get_settings
from app.tools.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPullTool,
    GitPushTool,
    GitStatusTool,
)


@pytest.fixture()
def sandbox(outside_tmp, monkeypatch):
    # outside any git repository, so "not a git directory" really holds
    monkeypatch.setenv("ALLOWED_DIRECTORIES", str(outside_tmp))
    get_settings.cache_clear()
    yield outside_tmp
    get_settings.cache_clear()


@pytest.fixture()
def repo(sandbox):
    subprocess.run(["git", "init", "-q"], cwd=sandbox, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=sandbox, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=sandbox, check=True)
    (sandbox / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=sandbox, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=sandbox, check=True)
    return sandbox


async def test_git_status_clean_repo(repo):
    result = await GitStatusTool().execute(path=str(repo))
    assert result.success is True
    assert "clean" in result.data["stdout"].lower() or "nothing to commit" in result.data["stdout"].lower()


async def test_git_status_with_uncommitted_change(repo):
    (repo / "new.txt").write_text("x")
    result = await GitStatusTool().execute(path=str(repo))
    assert result.success is True
    assert "new.txt" in result.data["stdout"]


async def test_git_log_shows_commit(repo):
    result = await GitLogTool().execute(path=str(repo))
    assert result.success is True
    assert "initial commit" in result.data["stdout"]


async def test_git_branch_lists_current_branch(repo):
    result = await GitBranchTool().execute(path=str(repo))
    assert result.success is True
    assert "*" in result.data["stdout"]


async def test_git_diff_shows_unstaged_changes(repo):
    (repo / "README.md").write_text("changed content")
    result = await GitDiffTool().execute(path=str(repo))
    assert result.success is True
    assert "changed content" in result.data["stdout"]


async def test_git_commit_requires_confirmation():
    assert GitCommitTool().security.value == "CONFIRM_REQUIRED"


async def test_git_push_requires_confirmation():
    assert GitPushTool().security.value == "CONFIRM_REQUIRED"


async def test_git_pull_requires_confirmation():
    assert GitPullTool().security.value == "CONFIRM_REQUIRED"


async def test_git_status_read_only_tools_are_safe():
    assert GitStatusTool().security.value == "SAFE"
    assert GitDiffTool().security.value == "SAFE"
    assert GitLogTool().security.value == "SAFE"
    assert GitBranchTool().security.value == "SAFE"


async def test_git_commit_creates_a_real_commit(repo):
    (repo / "file.txt").write_text("content")
    result = await GitCommitTool().execute(path=str(repo), message="add file", add_all=True)
    assert result.success is True

    log_result = await GitLogTool().execute(path=str(repo))
    assert "add file" in log_result.data["stdout"]


async def test_git_commit_rejects_empty_message(repo):
    result = await GitCommitTool().execute(path=str(repo), message="   ")
    assert result.success is False


async def test_git_outside_allowed_directory_is_refused(sandbox, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    result = await GitStatusTool().execute(path=str(outside))
    assert result.success is False
    assert "außerhalb der freigegebenen Ordner" in result.error


async def test_git_status_on_non_git_directory_fails_cleanly(sandbox):
    result = await GitStatusTool().execute(path=str(sandbox))
    assert result.success is False
