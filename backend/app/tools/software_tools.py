"""
Installed software and winget (FIX2 point 3).

    list        installed programs from the registry (fast, offline)   safe
    search      winget search                                         safe
    install     winget install    -> runs as a background command     critical
    uninstall   winget uninstall  -> runs as a background command     critical

Install/uninstall always ask while the safety prompt is on; the card shows
the exact winget command. Cypher itself never elevates: if an installer
needs admin rights, Windows shows its own UAC prompt and the user decides.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from app.pc import powershell
from app.security.classifier import CRITICAL, SAFE, user_profile, verdict
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

MAX_LIST = 60
_NAME_RE = re.compile(r"^[\w .+\-()]{1,100}$")
_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def installed_programs(filter_text: str = "") -> List[Dict[str, str]]:
    import winreg  # noqa: PLC0415

    wanted = filter_text.strip().lower()
    seen: Dict[str, Dict[str, str]] = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in _UNINSTALL_KEYS:
            try:
                root = winreg.OpenKey(hive, path)
            except OSError:
                continue
            with root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                            values = {}
                            for field in ("DisplayName", "DisplayVersion", "Publisher", "SystemComponent"):
                                try:
                                    values[field] = winreg.QueryValueEx(key, field)[0]
                                except OSError:
                                    pass
                    except OSError:
                        continue
                    name = str(values.get("DisplayName") or "").strip()
                    if not name or values.get("SystemComponent") == 1 or (wanted and wanted not in name.lower()):
                        continue
                    seen.setdefault(name.lower(), {"name": name, "version": str(values.get("DisplayVersion") or ""), "publisher": str(values.get("Publisher") or "")})
    return sorted(seen.values(), key=lambda p: p["name"].lower())


def winget_command(action: str, name: str) -> str:
    """The exact command line (shown on the approval card, run as a background job)."""
    quoted = "'" + name.replace("'", "''") + "'"
    target = f"--id {quoted} --exact" if "." in name and " " not in name else f"--name {quoted} --exact"
    if action == "install":
        return f"winget install {target} --accept-package-agreements --accept-source-agreements --disable-interactivity"
    return f"winget uninstall {target} --accept-source-agreements --disable-interactivity"


def _search(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(["winget", "search", "--query", name, "--accept-source-agreements", "--disable-interactivity"],
                          capture_output=True, timeout=60, creationflags=0x08000000)


class SoftwareArgs(ArgsModel):
    action: Literal["list", "search", "install", "uninstall"]
    name: Optional[str] = Field(None, max_length=100, description="Programmname oder winget-ID")


@register_tool
class SoftwareTool(Tool):
    name = "software"
    description = "Installierte Programme auflisten, per winget suchen, installieren oder deinstallieren."
    Args = SoftwareArgs
    risk = RiskTier.SAFE
    confirm_writes = False
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    def classify(self, args: Dict[str, Any], ctx=None):
        if args.get("action") in ("install", "uninstall"):
            return verdict(CRITICAL, "Software installieren/deinstallieren", "software")
        return verdict(SAFE)

    def describe(self, args: Dict[str, Any]) -> str:
        action, name = args.get("action", ""), args.get("name") or ""
        if action in ("install", "uninstall") and _NAME_RE.match(name):
            return winget_command(action, name)
        return {"list": "Installierte Programme", "search": "winget-Suche"}.get(action, "Software") + (f": {name}" if name else "")

    async def execute(self, db=None, action: str = "list", name: Optional[str] = None, **_) -> ToolResult:
        name = (name or "").strip()
        if action == "list":
            programs = await asyncio.to_thread(installed_programs, name)
            return ToolResult(success=True, data={"count": len(programs), "programs": programs[:MAX_LIST], "truncated": len(programs) > MAX_LIST})
        if not name or not _NAME_RE.match(name):
            return ToolResult(success=False, error="Bitte einen gültigen Programmnamen oder eine winget-ID angeben.")
        if action == "search":
            try:
                proc = await asyncio.to_thread(_search, name)
            except (OSError, subprocess.SubprocessError) as exc:
                return ToolResult(success=False, error=f"winget nicht verfügbar: {exc}")
            raw = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
            # winget draws a progress spinner with \r - keep what is left on each line.
            lines = [line.split("\r")[-1].rstrip() for line in raw.split("\n")]
            text, _cut = powershell.clip("\n".join(line for line in lines if line.strip()), 3000)
            return ToolResult(success=proc.returncode == 0, data={"results": text}, error=None if proc.returncode == 0 else f"Nichts gefunden für '{name}'.")
        if powershell.running_background() >= powershell.MAX_RUNNING_BACKGROUND:
            return ToolResult(success=False, error="Zu viele Hintergrund-Befehle laufen gerade.")
        command = winget_command(action, name)
        try:
            job = powershell.start(command, user_profile(), background=True)
        except OSError as exc:
            return ToolResult(success=False, error=f"winget konnte nicht gestartet werden: {exc}")
        return ToolResult(success=True, data={"job": job.id, "status": f"{'Installation' if action == 'install' else 'Deinstallation'} läuft im Hintergrund",
                                              "hint": f"Fortschritt mit command_status job={job.id}. Fragt Windows nach Adminrechten, entscheidest du im UAC-Fenster."})
