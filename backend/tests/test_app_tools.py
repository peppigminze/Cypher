from __future__ import annotations

from app.tools.app_tools import CloseApplicationTool, ListRunningApplicationsTool, OpenApplicationTool


async def test_list_running_applications_returns_process_entries():
    result = await ListRunningApplicationsTool().execute()
    assert result.success is True
    assert len(result.data) > 0
    assert "pid" in result.data[0] and "name" in result.data[0]


async def test_open_application_blocked_command_is_refused(monkeypatch):
    # Never let a regression in the denylist turn the test run into a real
    # shutdown: any process launch fails the test instead.
    launched = []
    monkeypatch.setattr("app.tools.app_tools.subprocess.Popen", lambda *a, **k: launched.append(a))
    result = await OpenApplicationTool().execute(command="shutdown /s /t 0")
    assert result.success is False
    assert "Refused" in result.error
    assert launched == []


async def test_close_application_requires_pid_or_name():
    result = await CloseApplicationTool().execute()
    assert result.success is False


async def test_close_application_unknown_pid():
    result = await CloseApplicationTool().execute(pid=999_999_999)
    assert result.success is False


async def test_close_application_unknown_name():
    result = await CloseApplicationTool().execute(name="definitely-not-a-real-process.exe")
    assert result.success is False
