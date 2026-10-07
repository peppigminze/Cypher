from __future__ import annotations

import asyncio
import platform
import shutil
from datetime import datetime, timezone

import psutil
from sqlalchemy.orm import Session

from app.config import get_settings
from app.tools.base import Tool, ToolResult, ToolSecurity


_WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_MONTHS_DE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


def local_now() -> datetime:
    """Current time in the user's time zone (TIMEZONE, else the PC's zone).

    The old tool returned UTC only and the model then answered
    "19:38 Uhr UTC" at 21:38 local time (docs/overhaul/AUDIT.md K3).
    """
    tz_name = get_settings().TIMEZONE
    if tz_name:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return datetime.now(ZoneInfo(tz_name))
    return datetime.now().astimezone()


def german_date(dt: datetime) -> str:
    return f"{_WEEKDAYS_DE[dt.weekday()]}, {dt.day}. {_MONTHS_DE[dt.month - 1]} {dt.year}"


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = "Aktuelle lokale Uhrzeit und Datum des Nutzers abfragen."
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        now = local_now()
        return ToolResult(
            success=True,
            data={
                "local_time": now.strftime("%H:%M"),
                "local_date": german_date(now),
                "local_iso": now.isoformat(timespec="seconds"),
                "timezone": now.tzname(),
                "utc_iso": now.astimezone(timezone.utc).isoformat(),
            },
        )


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


class GetSystemInfoTool(Tool):
    """Bundles get_current_time + cpu_usage + ram_usage into one call
    (Phase 6) - reuses GetCurrentTimeTool directly instead of
    re-deriving "now" itself, so there is exactly one place that decides
    what "current time" means. Exists because the native tool-calling
    pipeline's system prompt asks the model for at most one tool call
    per step (app/agent/native_tool_agent.py) - without this, a single
    "how's the PC doing" voice question would cost three separate
    tool-call round trips instead of one."""

    name = "get_system_info"
    description = (
        "Get a combined system snapshot: current date/time (UTC), CPU usage, and RAM "
        "free/total - one call instead of separate get_current_time/cpu_usage/ram_usage "
        "requests."
    )
    parameters = {"type": "object", "properties": {}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        try:
            time_result = await GetCurrentTimeTool().execute(db=db)
            cpu_percent = await asyncio.to_thread(psutil.cpu_percent, 0.3)
            vm = psutil.virtual_memory()
        except Exception as exc:  # noqa: BLE001 - psutil/platform calls, be defensive
            return ToolResult(success=False, error=f"Could not read system info: {exc}")

        if not time_result.success:
            return ToolResult(success=False, error=time_result.error or "Could not determine the current time.")

        return ToolResult(
            success=True,
            data={
                "utc_iso": time_result.data["utc_iso"],
                "local_time": time_result.data.get("local_time"),
                "local_date": time_result.data.get("local_date"),
                "cpu_percent": cpu_percent,
                "ram_total_gb": round(vm.total / 1e9, 2),
                "ram_free_gb": round(vm.available / 1e9, 2),
                "ram_percent_used": vm.percent,
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
