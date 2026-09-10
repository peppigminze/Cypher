from __future__ import annotations

import asyncio
import platform
import shutil
from datetime import datetime, timezone

import psutil
from sqlalchemy.orm import Session

from app.tools.base import Tool, ToolResult, ToolSecurity


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = "Get the current date and time (UTC)."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        now = datetime.now(timezone.utc)
        return ToolResult(success=True, data={"utc_iso": now.isoformat()})


class GetSystemStatusTool(Tool):
    name = "get_system_status"
    description = "Get basic status info about the machine Cypher is running on."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        data = {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "machine": platform.machine(),
        }
        return ToolResult(success=True, data=data)


class CpuUsageTool(Tool):
    name = "cpu_usage"
    description = "Get current CPU usage as a percentage."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        # A short blocking sample gives a real reading instead of the
        # meaningless 0.0% psutil returns for interval=None on the very
        # first call of a process. Runs off the event loop thread so it
        # doesn't stall other concurrent requests for 0.3s.
        percent = await asyncio.to_thread(psutil.cpu_percent, 0.3)
        return ToolResult(success=True, data={"cpu_percent": percent, "cpu_count": psutil.cpu_count()})


class RamUsageTool(Tool):
    name = "ram_usage"
    description = "Get current RAM usage."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        vm = psutil.virtual_memory()
        return ToolResult(
            success=True,
            data={
                "total_gb": round(vm.total / 1e9, 2),
                "used_gb": round(vm.used / 1e9, 2),
                "percent": vm.percent,
            },
        )


class DiskUsageTool(Tool):
    name = "disk_usage"
    description = "Get disk usage for a drive/path (defaults to the system drive)."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, path: str = "C:\\", **_) -> ToolResult:
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            data={
                "path": path,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
            },
        )


class NetworkStatusTool(Tool):
    name = "network_status"
    description = "List which network interfaces are currently up."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        stats = psutil.net_if_stats()
        interfaces = [{"name": name, "is_up": s.isup} for name, s in stats.items()]
        return ToolResult(success=True, data={"interfaces": interfaces})
