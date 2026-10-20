"""
run_command / command_status with a REAL PowerShell (FIX2 point 3): output,
exit codes, UTF-8, timeout, barge-in cancel kills the whole process tree,
background jobs, the admin-rights hint, and Cypher's secrets staying out
of the child environment. Only harmless commands run here.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import psutil
import pytest

from app.config import get_settings
from app.database.db import Base, SessionLocal, engine
from app.database.models import CommandLog
from app.pc import elevation, powershell
from app.tools.base import RiskTier
from app.tools.shell_tools import CommandStatusTool, RunCommandTool

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell tools are Windows-only")


@pytest.fixture()
def db_tables():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


async def test_runs_powershell_with_utf8_and_logs(db_tables):
    result = await RunCommandTool().execute(command="Write-Output 'Grüße aus Cypher'")
    assert result.success is True, result.error
    assert result.data["exit_code"] == 0
    assert "Grüße aus Cypher" in result.data["output"]
    await asyncio.sleep(0.3)  # the watcher thread writes the log entry
    with SessionLocal() as db:
        logs = db.query(CommandLog).all()
    assert logs and logs[-1].command == "Write-Output 'Grüße aus Cypher'" and logs[-1].exit_code == 0


async def test_default_working_directory_is_the_user_profile():
    result = await RunCommandTool().execute(command="(Get-Location).Path")
    assert os.path.normcase(result.data["output"].strip()) == os.path.normcase(os.path.realpath(os.environ["USERPROFILE"]))


async def test_nonzero_exit_code_and_readable_errors():
    result = await RunCommandTool().execute(command="exit 3")
    assert result.success is False and result.data["exit_code"] == 3
    failing = await RunCommandTool().execute(command="Get-Item 'C:\\gibt-es-nicht-cypher'")
    assert failing.success is False
    assert "CLIXML" not in failing.data["errors"] and "gibt-es-nicht-cypher" in failing.data["errors"]


async def test_missing_working_directory_is_reported():
    result = await RunCommandTool().execute(command="Get-Date", cwd="C:\\gibt-es-nicht-cypher")
    assert result.success is False and "gibt es nicht" in result.error


async def test_timeout_kills_the_command():
    started = time.monotonic()
    result = await RunCommandTool().execute(command="Start-Sleep -Seconds 20", timeout=1)
    assert time.monotonic() - started < 8
    assert result.success is False and "Zeitlimit" in result.error


async def test_barge_in_cancel_kills_the_whole_process_tree(monkeypatch):
    jobs = []
    real_start = powershell.start
    monkeypatch.setattr(powershell, "start", lambda *a, **k: jobs.append(real_start(*a, **k)) or jobs[-1])
    # ping is a separate child process of powershell.exe - it must die too.
    task = asyncio.create_task(RunCommandTool().execute(command="ping -n 30 127.0.0.1"))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if jobs and psutil.Process(jobs[0].pid).children(recursive=True):
            break
    job = jobs[0]
    children = [c.pid for c in psutil.Process(job.pid).children(recursive=True)]
    assert children, "ping should run as a child of powershell"
    task.cancel()  # what the voice session does on barge-in
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _gone(job.pid) and all(_gone(pid) for pid in children)
    assert job.stopped == "cancelled"


async def test_background_job_status_and_stop():
    started = await RunCommandTool().execute(command="Start-Sleep -Milliseconds 300; Write-Output 'fertig'", background=True)
    assert started.success and started.data["job"].startswith("j")
    job_id = started.data["job"]
    for _ in range(100):
        status = await CommandStatusTool().execute(job=job_id)
        if status.data["status"] != "läuft":
            break
        await asyncio.sleep(0.05)
    assert status.data["status"] == "fertig" and "fertig" in status.data["output"] and status.data["exit_code"] == 0

    long = await RunCommandTool().execute(command="Start-Sleep -Seconds 30", background=True)
    pid = powershell.get(long.data["job"]).pid
    listing = await CommandStatusTool().execute()
    assert any(j["job"] == long.data["job"] and j["status"] == "läuft" for j in listing.data["jobs"])
    stopped = await CommandStatusTool().execute(job=long.data["job"], stop=True)
    assert stopped.data["status"] == "abgebrochen" and _gone(pid)
    unknown = await CommandStatusTool().execute(job="j99999")
    assert unknown.success is False


async def test_admin_rights_are_reported_with_instructions():
    result = await RunCommandTool().execute(command="Write-Error 'Zugriff verweigert'; exit 5")
    assert result.success is False and result.data.get("needs_admin") is True
    assert "Administratorrechte" in result.error and "Als Administrator ausführen" in result.error


async def test_refuses_to_run_when_cypher_itself_is_elevated(monkeypatch):
    started = []
    monkeypatch.setattr(elevation, "is_elevated", lambda: True)
    monkeypatch.setattr(powershell, "start", lambda *a, **k: started.append(a))
    result = await RunCommandTool().execute(command="Get-Date")
    assert result.success is False and "Administratorrechten" in result.error and started == []


async def test_cyphers_secrets_are_not_passed_to_commands(monkeypatch):
    monkeypatch.setenv("USER_TOKEN", "geheim-token-123")
    monkeypatch.setenv("CLOUD_LLM_API_KEY", "sk-geheim")
    result = await RunCommandTool().execute(command="Write-Output \"[$env:USER_TOKEN][$env:CLOUD_LLM_API_KEY][$env:USERNAME]\"")
    assert "geheim" not in result.data["output"]
    assert "[][]" in result.data["output"] and os.environ["USERNAME"] in result.data["output"]


async def test_long_output_is_clipped_for_the_model():
    limit = get_settings().FETCH_MAX_CHARS
    result = await RunCommandTool().execute(command="1..3000 | ForEach-Object { 'Zeile ' + $_ }")
    assert result.data["truncated"] is True
    assert len(result.data["output"]) < limit + 100
    assert "Zeile 1" in result.data["output"] and "Zeile 3000" in result.data["output"]  # head and tail


def test_classification_comes_from_the_classifier():
    tool = RunCommandTool()
    assert tool.classify({"command": "Get-Process"}).level == RiskTier.SAFE
    assert tool.classify({"command": "Remove-Item -Recurse -Force C:\\Users\\x\\Alt"}).level == RiskTier.CRITICAL
    assert tool.classify({"command": "Start-Process powershell -Verb RunAs"}).level == RiskTier.BLOCKED  # never elevate
    # the working directory is part of the rating: reading Cypher's logs is
    # critical, writing there (audit log) is on the red line
    logs = str(get_settings().log_dir_path)
    assert tool.classify({"command": "Get-Content x", "cwd": logs}).level == RiskTier.CRITICAL
    assert "cypher_secrets" in tool.classify({"command": "Set-Content audit.jsonl 'x'", "cwd": logs}).categories


def test_clixml_is_turned_into_text():
    raw = '#< CLIXML\r\n<Objs Version="1.1.0.1"><S S="Error">Fehler: &lt;nope&gt;_x000D__x000A_</S><S S="Error">Zeile 2</S></Objs>'
    assert powershell.clixml_to_text(raw) == "Fehler: <nope>\nZeile 2"
    assert powershell.clixml_to_text("plain") == "plain"
