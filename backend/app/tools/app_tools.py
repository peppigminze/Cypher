"""
Application control tools (project spec section 13). Listing running
processes is read-only (SAFE). Opening/closing a process can affect
unsaved work or launch arbitrary programs, so both require confirmation
(project spec section 14) - open_application additionally passes
through the same command denylist as run_command, since a shell launch
string is just as powerful as a shell command.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from typing import Optional

import psutil
from sqlalchemy.orm import Session

from pydantic import Field

from app.config import get_settings
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool
from app.tools.command_safety import is_blocked_command

MAX_PROCESS_LIST = 200


@register_tool
class ListRunningApplicationsTool(Tool):
    name = "list_running_applications"
    description = "List currently running processes (name and PID)."
    parameters = {"type": "object", "properties": {}}
    risk = RiskTier.READ_ONLY

    async def execute(self, db: Session | None = None, **_) -> ToolResult:
        processes = []
        for proc in psutil.process_iter(["pid", "name"]):
            processes.append({"pid": proc.info["pid"], "name": proc.info["name"]})
            if len(processes) >= MAX_PROCESS_LIST:
                break
        return ToolResult(success=True, data=processes)


@register_tool
class OpenApplicationTool(Tool):
    name = "open_application"
    description = (
        "Launch a program on the user's PC (e.g. 'notepad.exe' or a full path). "
        "Requires confirmation."
    )
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}
    risk = RiskTier.DESTRUCTIVE

    async def execute(self, db: Session | None = None, command: str = "", **_) -> ToolResult:
        blocked_reason = is_blocked_command(command)
        if blocked_reason:
            return ToolResult(success=False, error=f"Refused - matches a blocked pattern ({blocked_reason}).")
        try:
            proc = subprocess.Popen(command, shell=True)
        except OSError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"launched": command, "pid": proc.pid})


@register_tool
class CloseApplicationTool(Tool):
    name = "close_application"
    description = "Close a running application by PID or process name. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {"pid": {"type": "integer"}, "name": {"type": "string"}},
    }
    risk = RiskTier.DESTRUCTIVE

    async def execute(
        self, db: Session | None = None, pid: int | None = None, name: str | None = None, **_
    ) -> ToolResult:
        if pid is None and not name:
            return ToolResult(success=False, error="Provide either 'pid' or 'name'.")

        targets: list[psutil.Process] = []
        if pid is not None:
            try:
                targets = [psutil.Process(pid)]
            except psutil.NoSuchProcess:
                return ToolResult(success=False, error=f"No process with PID {pid}.")
        else:
            targets = [
                p
                for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower() == name.lower()
            ]
            if not targets:
                return ToolResult(success=False, error=f"No running process named '{name}'.")

        closed: list[int] = []
        for proc in targets:
            try:
                proc.terminate()
                closed.append(proc.pid)
            except psutil.NoSuchProcess:
                continue

        # Give terminated processes a brief moment before reporting, so
        # a caller listing processes right after sees them gone.
        await asyncio.sleep(0.2)
        return ToolResult(success=True, data={"closed_pids": closed})


class OpenAppArgs(ArgsModel):
    name: str = Field(..., min_length=1, max_length=60, description="Name der App, z. B. 'spotify', 'rechner', 'editor'")


def _match_app(name: str, whitelist: dict[str, str]) -> Optional[str]:
    wanted = re.sub(r"\b(den|die|das|mir|bitte|app|programm)\b", "", name.lower()).strip(" .!?")
    if wanted in whitelist:
        return wanted
    candidates = [key for key in whitelist if key.startswith(wanted) or wanted.startswith(key) or key in wanted]
    return candidates[0] if len(candidates) == 1 else None


@register_tool
class OpenAppTool(Tool):
    """Starts ONLY apps from APP_WHITELIST (name -> exe/URI). The model picks
    a name, never a command line - so there is nothing to inject."""

    name = "open_app"
    description = "Eine freigegebene Anwendung starten, z. B. Spotify, Rechner, Editor, Explorer, Browser, Einstellungen."
    Args = OpenAppArgs
    risk = RiskTier.CONTROL
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings) -> bool:
        return sys.platform == "win32" and bool(settings.app_whitelist_map)

    def describe(self, args) -> str:
        return f"App starten: {args.get('name', '')}"

    async def execute(self, db: Session | None = None, name: str = "", **_) -> ToolResult:
        whitelist = get_settings().app_whitelist_map
        key = _match_app(name, whitelist)
        if key is None:
            return ToolResult(success=False, error=f"'{name}' ist nicht freigegeben. Verfügbar: {', '.join(sorted(whitelist))}. (Erweiterbar über APP_WHITELIST in der .env.)")
        target = whitelist[key]
        try:
            await asyncio.to_thread(os.startfile, target)  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            return ToolResult(success=False, error=f"'{key}' konnte nicht gestartet werden: {exc}")
        return ToolResult(success=True, data={"app": key})
