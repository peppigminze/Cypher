"""
File tools without the sandbox (FIX2 point 3): they work everywhere, the
classifier decides the risk. Deleting and overwriting go to the recycle
bin - here a fake bin (the real one is used in the smoke test,
docs/overhaul/FIX2-report.md), so test runs don't fill the user's bin.
"""
from __future__ import annotations

import os
import shutil

import pytest

from app.config import PROJECT_ROOT, get_settings
from app.pc.known_folders import known_folder
from app.tools import file_tools
from app.tools.base import RiskTier
from app.tools.context import ToolContext
from app.tools.file_tools import FileOpTool, ListFilesTool, ReadFileTool, WriteFileTool
from app.tools.search_tools import FindFilesTool


@pytest.fixture()
def fake_bin(tmp_path, monkeypatch):
    """send2trash replaced by a move into tmp_path/_bin (records every call)."""
    bin_dir = tmp_path / "_bin"
    bin_dir.mkdir()
    trashed = []

    def fake_trash(path):
        trashed.append(path)
        shutil.move(path, str(bin_dir / os.path.basename(path)))

    monkeypatch.setattr(file_tools, "_trash", fake_trash)
    return trashed


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"  # tmp_path lies in the user profile (AppData\Local\Temp)
    folder.mkdir()
    return folder


# ------------------------------------------------------------------ behaviour
async def test_write_read_append(work):
    target = work / "sub" / "hallo.txt"
    assert (await WriteFileTool().execute(path=str(target), content="Hallo")).success
    assert (await WriteFileTool().execute(path=str(target), content=" Cypher", mode="append")).success
    read = await ReadFileTool().execute(path=str(target))
    assert read.success and read.data["content"] == "Hallo Cypher"


async def test_create_refuses_existing_and_overwrite_keeps_old_version_in_bin(work, fake_bin):
    target = work / "brief.txt"
    target.write_text("alt", encoding="utf-8")
    refused = await WriteFileTool().execute(path=str(target), content="neu")
    assert refused.success is False and "overwrite" in refused.error
    done = await WriteFileTool().execute(path=str(target), content="neu", mode="overwrite")
    assert done.success and done.data["old_version"] == "im Papierkorb"
    assert target.read_text(encoding="utf-8") == "neu" and fake_bin == [str(target)]


async def test_read_limits_and_binary(work, monkeypatch):
    monkeypatch.setenv("FILE_READ_MAX_BYTES", "1000")
    get_settings.cache_clear()
    try:
        (work / "big.log").write_text("a" * 50_000, encoding="utf-8")
        big = await ReadFileTool().execute(path=str(work / "big.log"))
        assert big.success and big.data["truncated"] is True and len(big.data["content"]) == 1000
    finally:
        get_settings.cache_clear()
    (work / "bild.bin").write_bytes(b"\x89PNG\x00\x00binary")
    binary = await ReadFileTool().execute(path=str(work / "bild.bin"))
    assert binary.success is False and "keine Textdatei" in binary.error


async def test_write_size_limit(work, monkeypatch):
    monkeypatch.setenv("FILE_WRITE_MAX_BYTES", "1000")
    get_settings.cache_clear()
    try:
        result = await WriteFileTool().execute(path=str(work / "x.txt"), content="y" * 5000)
    finally:
        get_settings.cache_clear()
    assert result.success is False and "zu groß" in result.error


async def test_list_files_folders_first(work):
    (work / "b.txt").write_text("x", encoding="utf-8")
    (work / "a_ordner").mkdir()
    result = await ListFilesTool().execute(path=str(work))
    assert [e["name"] for e in result.data["entries"]] == ["a_ordner", "b.txt"]
    assert (await ListFilesTool().execute(path=str(work / "fehlt"))).success is False


async def test_file_op_copy_move_rename_mkdir(work):
    (work / "a.txt").write_text("A", encoding="utf-8")
    assert (await FileOpTool().execute(action="mkdir", path=str(work / "ziel"))).success
    copied = await FileOpTool().execute(action="copy", path=str(work / "a.txt"), to=str(work / "ziel"))
    assert copied.success and (work / "ziel" / "a.txt").read_text(encoding="utf-8") == "A"  # "copy into the folder"
    again = await FileOpTool().execute(action="copy", path=str(work / "a.txt"), to=str(work / "ziel"))
    assert again.success is False and "nicht überschrieben" in again.error
    assert (await FileOpTool().execute(action="rename", path=str(work / "a.txt"), to="b.txt")).success
    assert (work / "b.txt").exists() and not (work / "a.txt").exists()
    assert (await FileOpTool().execute(action="move", path=str(work / "b.txt"), to=str(work / "ziel" / "c.txt"))).success
    assert (work / "ziel" / "c.txt").exists()


async def test_delete_goes_to_the_recycle_bin_never_permanent(work, fake_bin):
    victim = work / "weg.txt"
    victim.write_text("x", encoding="utf-8")
    result = await FileOpTool().execute(action="delete", path=str(victim))
    assert result.success and result.data["recycle_bin"] is True
    assert fake_bin == [str(victim)] and not victim.exists()


async def test_find_files_in_any_folder(work):
    (work / "Bewerbung_2026.docx").write_text("x", encoding="utf-8")
    result = await FindFilesTool().execute(query="bewerbung", folder=str(work))
    assert result.success and result.data["count"] == 1


# ------------------------------------------------------------------ risk
def test_everyday_file_work_in_the_profile_runs_without_asking(work):
    assert ListFilesTool().classify({"path": str(work)}).level == RiskTier.SAFE
    assert ReadFileTool().classify({"path": str(work / "a.txt")}).level == RiskTier.SAFE
    assert WriteFileTool().classify({"path": str(work / "neu.txt"), "content": "x"}).level == RiskTier.WRITE
    assert FileOpTool().classify({"action": "delete", "path": str(work / "a.txt")}).level == RiskTier.WRITE
    assert WriteFileTool.confirm_writes is False and FileOpTool.confirm_writes is False


def test_read_file_asks_when_the_content_would_go_to_the_cloud(work):
    assert ReadFileTool().classify({"path": str(work / "a.txt")}, ToolContext(provider="cloud")).level == RiskTier.WRITE
    assert ReadFileTool.confirm_writes is True  # so that `write` really asks


def test_critical_file_operations(work):
    windows = os.environ.get("SystemRoot", r"C:\Windows")
    whole_folder = FileOpTool().classify({"action": "delete", "path": str(work)})
    assert whole_folder.level == RiskTier.CRITICAL and "folder_delete" in whole_folder.categories
    system = FileOpTool().classify({"action": "delete", "path": os.path.join(windows, "System32", "drivers")})
    assert "system_folders" in system.categories  # red line
    outside = WriteFileTool().classify({"path": "C:\\cypher-test.txt", "content": "x"})
    assert outside.level == RiskTier.CRITICAL and "outside_profile" in outside.categories
    secrets = WriteFileTool().classify({"path": str(PROJECT_ROOT / ".env"), "content": "x"})
    assert "cypher_secrets" in secrets.categories  # red line
    ssh = ReadFileTool().classify({"path": os.path.join(os.environ["USERPROFILE"], ".ssh", "id_ed25519")})
    assert ssh.level == RiskTier.CRITICAL and "credentials" in ssh.categories
    own_code = FileOpTool().classify({"action": "move", "path": str(PROJECT_ROOT / "backend" / "app" / "main.py"), "to": str(work / "x.py")})
    assert own_code.level == RiskTier.CRITICAL and "cypher_files" in own_code.categories
    startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "evil.bat")
    assert "autostart" in WriteFileTool().classify({"path": startup, "content": "x"}).categories
    assert FindFilesTool().classify({"query": "x", "folder": os.path.join(os.environ["USERPROFILE"], ".ssh")}).level == RiskTier.CRITICAL


def test_known_folder_names_mean_the_real_folders():
    documents = known_folder("documents")
    assert documents
    from app.security.classifier import resolve_path

    assert resolve_path("Dokumente\\Bericht.docx") == os.path.realpath(os.path.join(documents, "Bericht.docx"))
    assert resolve_path("Desktop") == os.path.realpath(known_folder("desktop"))
