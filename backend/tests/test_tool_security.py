"""
Tests for the PC-tool safety layer (Phase 5): ALLOWED_DIRECTORIES
sandboxing (app/tools/paths.py) and the command denylist
(app/tools/command_safety.py). These are the most safety-critical
pieces in the whole project - a bug here means a path or command
escapes the sandbox Cypher is supposed to be confined to.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.tools.command_safety import is_blocked_command
from app.tools.paths import PathNotAllowedError, resolve_allowed_path


@pytest.fixture()
def sandboxed(tmp_path, monkeypatch):
    """Scopes ALLOWED_DIRECTORIES to a fresh tmp_path for one test."""
    monkeypatch.setenv("ALLOWED_DIRECTORIES", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def no_allowed_dirs(monkeypatch):
    monkeypatch.setenv("ALLOWED_DIRECTORIES", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------- resolve_allowed_path


def test_path_inside_allowed_directory_resolves(sandboxed):
    (sandboxed / "notes.txt").write_text("hi")
    resolved = resolve_allowed_path(str(sandboxed / "notes.txt"))
    assert resolved == (sandboxed / "notes.txt").resolve()


def test_relative_path_resolves_against_first_allowed_directory(sandboxed):
    resolved = resolve_allowed_path("notes.txt")
    assert resolved == (sandboxed / "notes.txt").resolve()


def test_path_traversal_outside_allowed_directory_is_rejected(sandboxed):
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(str(sandboxed / ".." / "outside.txt"))


def test_sibling_directory_with_similar_prefix_is_rejected(tmp_path, monkeypatch):
    """'/allowed' must not accidentally also allow '/allowed-evil' just
    because it starts with the same string."""
    allowed = tmp_path / "allowed"
    evil = tmp_path / "allowed-evil"
    allowed.mkdir()
    evil.mkdir()
    monkeypatch.setenv("ALLOWED_DIRECTORIES", str(allowed))
    get_settings.cache_clear()
    try:
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path(str(evil / "file.txt"))
    finally:
        get_settings.cache_clear()


def test_completely_unrelated_absolute_path_is_rejected(sandboxed):
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path("C:\\Windows\\System32\\drivers\\etc\\hosts")


def test_no_allowed_directories_configured_rejects_everything(no_allowed_dirs, tmp_path):
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(str(tmp_path))


def test_second_allowed_directory_also_works(tmp_path, monkeypatch):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    monkeypatch.setenv("ALLOWED_DIRECTORIES", f"{dir_a},{dir_b}")
    get_settings.cache_clear()
    try:
        resolved = resolve_allowed_path(str(dir_b / "x.txt"))
        assert resolved == (dir_b / "x.txt").resolve()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- is_blocked_command


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "RM -RF /",
        "format c:",
        "del /s /q C:\\",
        "mkfs.ext4 /dev/sda1",
        "shutdown /s /t 0",
        "Restart-Computer -Force",
        ":(){ :|:& };:",
        "reg delete HKLM\\Software\\Whatever /f",
    ],
)
def test_dangerous_commands_are_blocked(command):
    assert is_blocked_command(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        "git status",
        "npm install",
        "ls -la",
        "python script.py",
        "dir",
    ],
)
def test_benign_commands_are_not_blocked(command):
    assert is_blocked_command(command) is None
