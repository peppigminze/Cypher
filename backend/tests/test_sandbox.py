"""ALLOWED_DIRECTORIES sandbox (app/security/paths.py): traversal,
junction/symlink escapes, Windows path tricks, sensitive files.

Since FIX2 only the git tools are confined to it; the file tools work on
all drives and are rated by app/security/classifier.py (tests/test_file_tools.py)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.config import get_settings
from app.security.paths import PathNotAllowedError, resolve_allowed_path


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("geheim", encoding="utf-8")
    monkeypatch.setenv("ALLOWED_DIRECTORIES", str(allowed))
    get_settings.cache_clear()
    yield allowed, outside
    get_settings.cache_clear()


def test_relative_path_resolves_inside_root(sandbox):
    allowed, _ = sandbox
    assert resolve_allowed_path("notes/a.txt") == allowed / "notes" / "a.txt"


@pytest.mark.parametrize("path", ["..\\outside\\secret.txt", "../outside/secret.txt", "sub\\..\\..\\outside\\secret.txt"])
def test_traversal_is_refused(sandbox, path):
    with pytest.raises(PathNotAllowedError):
        resolve_allowed_path(path)


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("notiz.txt:versteckt", "Doppelpunkt"),
        ("NUL", "Gerätenamen"),
        ("sub\\con.txt", "Gerätenamen"),
        ("\\\\server\\share\\x.txt", "Netzwerkpfade"),
        ("", "kein Pfad"),
    ],
)
def test_windows_path_tricks_are_refused(sandbox, path, fragment):
    with pytest.raises(PathNotAllowedError, match=fragment):
        resolve_allowed_path(path)


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.pem", "passwords.kdbx", "secrets.json", "jarvis.db"])
def test_sensitive_files_inside_allowed_root_are_refused(sandbox, name):
    allowed, _ = sandbox
    (allowed / name).write_text("x", encoding="utf-8")
    with pytest.raises(PathNotAllowedError, match="gesperrt"):
        resolve_allowed_path(name)


def test_key_folders_and_appdata_are_refused(sandbox):
    allowed, _ = sandbox
    for folder in (".ssh", "AppData\\Roaming\\Mozilla"):
        target = allowed / folder
        target.mkdir(parents=True)
        (target / "file.txt").write_text("x", encoding="utf-8")
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path(f"{folder}\\file.txt")


def test_windows_system_folder_refused_even_if_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_DIRECTORIES", os.environ.get("SystemDrive", "C:") + "\\")
    get_settings.cache_clear()
    try:
        with pytest.raises(PathNotAllowedError, match="System"):
            resolve_allowed_path(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "win.ini"))
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_junction_pointing_outside_is_refused(sandbox):
    allowed, outside = sandbox
    link = allowed / "harmlos"
    # Junctions need no admin rights (unlike symlinks).
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(PathNotAllowedError, match="außerhalb"):
            resolve_allowed_path("harmlos\\secret.txt")
    finally:
        os.rmdir(link)
