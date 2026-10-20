from __future__ import annotations

from app.tools import system_tools
from app.tools.base import ToolResult
from app.tools.system_tools import CpuUsageTool, DiskUsageTool, GetSystemInfoTool, NetworkStatusTool, RamUsageTool


async def test_cpu_usage():
    result = await CpuUsageTool().execute()
    assert result.success is True
    assert 0.0 <= result.data["cpu_percent"] <= 100.0
    assert result.data["cpu_count"] >= 1


async def test_ram_usage():
    result = await RamUsageTool().execute()
    assert result.success is True
    assert result.data["total_gb"] > 0
    assert 0.0 <= result.data["percent"] <= 100.0


async def test_disk_usage():
    result = await DiskUsageTool().execute(path="C:\\")
    assert result.success is True
    assert result.data["total_gb"] > 0


async def test_disk_usage_invalid_path():
    result = await DiskUsageTool().execute(path="Z:\\this-drive-should-not-exist\\")
    assert result.success is False


async def test_network_status():
    result = await NetworkStatusTool().execute()
    assert result.success is True
    assert isinstance(result.data["interfaces"], list)


async def test_get_system_info_combines_time_cpu_and_ram():
    result = await GetSystemInfoTool().execute()
    assert result.success is True
    assert "utc_iso" in result.data
    assert result.data["ram_total_gb"] > 0
    assert 0.0 <= result.data["ram_percent_used"] <= 100.0
    assert 0.0 <= result.data["cpu_percent"] <= 100.0


async def test_get_system_info_reports_time_failure_cleanly(monkeypatch):
    class FailingTimeTool:
        async def execute(self, db=None, **_):
            return ToolResult(success=False, error="clock unavailable")

    monkeypatch.setattr(system_tools, "GetCurrentTimeTool", lambda: FailingTimeTool())
    result = await GetSystemInfoTool().execute()
    assert result.success is False
    assert "clock unavailable" in result.error


async def test_get_system_info_reports_psutil_failure_cleanly(monkeypatch):
    def failing_cpu_percent(*_args, **_kwargs):
        raise RuntimeError("psutil boom")

    monkeypatch.setattr(system_tools.psutil, "cpu_percent", failing_cpu_percent)
    result = await GetSystemInfoTool().execute()
    assert result.success is False
    assert "Could not read system info" in result.error
