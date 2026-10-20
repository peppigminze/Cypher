"""
PowerShell on this PC (FIX2 point 3): run_command + command_status.

Risk is decided per command by the deterministic classifier
(app/security/classifier.py classify_powershell) - Get-Process runs right
away, Remove-Item -Recurse or anything unknown is `critical` and asks
(app/security/policy.py). The model never decides that.

    foreground   waits up to `timeout` seconds (default TERMINAL_TIMEOUT_SECONDS);
                 a barge-in or tool timeout cancels the turn and kills the
                 whole process tree (app/pc/powershell.py)
    background   returns a job id at once; command_status shows output/stops it
Output for the model is clipped (head + tail, FETCH_MAX_CHARS); the full
output goes to command_logs. Cypher runs without admin rights; if a command
needs them, the result says so and what the user can do (app/pc/elevation.py).
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, Optional

from pydantic import Field

from app.config import get_settings
from app.pc import elevation, powershell
from app.security.classifier import CONTROL, classify_path, classify_powershell, resolve_path, user_profile, verdict, worst
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool

STATUS_TAIL_CHARS = 2000


class RunCommandArgs(ArgsModel):
    command: str = Field(..., min_length=1, max_length=4000, description="PowerShell-Befehl")
    cwd: Optional[str] = Field(None, description="Arbeitsordner (Standard: Benutzerordner)")
    background: bool = Field(False, description="true für lange Befehle; Ergebnis später mit command_status")
    timeout: Optional[int] = Field(None, ge=1, le=300, description="Sekunden, Standard 30")


def _cwd(raw: Optional[str]) -> str:
    return resolve_path(raw) if raw and raw.strip() else user_profile()


@register_tool
class RunCommandTool(Tool):
    name = "run_command"
    description = "PowerShell-Befehl auf diesem Windows-PC ausführen, für alles ohne eigenes Werkzeug. Liefert die Ausgabe."
    Args = RunCommandArgs
    risk = RiskTier.CRITICAL  # the class default; the classifier rates every call
    confirm_writes = False
    exposed_by_default = True
    timeout_seconds = 320.0  # the command's own timeout (max 300 s) ends it first

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    def classify(self, args: Dict[str, Any], ctx=None):
        cwd = _cwd(args.get("cwd"))
        return worst([classify_powershell(args.get("command", ""), cwd=cwd), classify_path(cwd, "list")])

    def describe(self, args: Dict[str, Any]) -> str:
        where = f" (in {args['cwd']})" if args.get("cwd") else ""
        mode = " [im Hintergrund]" if args.get("background") else ""
        return f"PowerShell: {args.get('command', '')}{where}{mode}"

    async def execute(self, db=None, command: str = "", cwd: Optional[str] = None, background: bool = False, timeout: Optional[int] = None, **_) -> ToolResult:
        if elevation.is_elevated():
            return ToolResult(success=False, error="Cypher läuft mit Administratorrechten - das ist nicht vorgesehen. Bitte Cypher normal starten (nicht 'Als Administrator ausführen').")
        folder = _cwd(cwd)
        if not os.path.isdir(folder):
            return ToolResult(success=False, error=f"Den Ordner '{folder}' gibt es nicht.")
        settings = get_settings()
        if background and powershell.running_background() >= powershell.MAX_RUNNING_BACKGROUND:
            return ToolResult(success=False, error=f"Es laufen schon {powershell.MAX_RUNNING_BACKGROUND} Befehle im Hintergrund. Erst einen beenden (command_status mit stop=true).")
        try:
            job = powershell.start(command, folder, background=background)
        except OSError as exc:
            return ToolResult(success=False, error=f"PowerShell konnte nicht gestartet werden: {exc}")
        if background:
            return ToolResult(success=True, data={"job": job.id, "status": "läuft im Hintergrund", "hint": f"Ausgabe/Status mit command_status job={job.id}"})

        limit = timeout or settings.TERMINAL_TIMEOUT_SECONDS
        await powershell.wait(job, limit)
        output, cut_out = powershell.clip(job.stdout().strip(), settings.FETCH_MAX_CHARS)
        errors, cut_err = powershell.clip(job.stderr().strip(), max(500, settings.FETCH_MAX_CHARS // 3))
        data: Dict[str, Any] = {"exit_code": job.exit_code, "output": output, "seconds": round(job.duration(), 1)}
        if errors:
            data["errors"] = errors
        if cut_out or cut_err or job.truncated:
            data["truncated"] = True
        if job.stopped == "timeout":
            return ToolResult(success=False, data=data, error=f"Nach {limit:.0f} s abgebrochen (Zeitlimit). Für lange Befehle background=true verwenden.")
        if job.exit_code != 0 and elevation.needs_admin(f"{output}\n{errors}", job.exit_code):
            data["needs_admin"] = True
            return ToolResult(success=False, data=data, error=elevation.admin_hint(command))
        if job.exit_code != 0:
            return ToolResult(success=False, data=data, error=(errors.splitlines()[0][:300] if errors else f"Exit-Code {job.exit_code}"))
        return ToolResult(success=True, data=data)


class CommandStatusArgs(ArgsModel):
    job: Optional[str] = Field(None, description="Job-ID, z. B. j3; leer = alle")
    stop: bool = Field(False, description="true = Befehl abbrechen")


def _job_info(job: powershell.Job, tail: bool) -> Dict[str, Any]:
    status = "läuft" if job.running else ("abgebrochen" if job.stopped else "fertig")
    info: Dict[str, Any] = {"job": job.id, "command": job.command[:200], "status": status, "seconds": round(job.duration(), 1)}
    if not job.running:
        info["exit_code"] = job.exit_code
    if tail:
        out = job.stdout().strip()
        info["output"] = out[-STATUS_TAIL_CHARS:]
        if len(out) > STATUS_TAIL_CHARS:
            info["truncated"] = True
        err = job.stderr().strip()
        if err:
            info["errors"] = err[-STATUS_TAIL_CHARS // 2:]
    return info


@register_tool
class CommandStatusTool(Tool):
    name = "command_status"
    description = "Status und Ausgabe von Hintergrund-Befehlen (run_command background) abfragen oder einen abbrechen."
    Args = CommandStatusArgs
    risk = RiskTier.SAFE
    confirm_writes = False
    exposed_by_default = True

    @classmethod
    def is_available(cls, settings: Any) -> bool:
        return sys.platform == "win32"

    def classify(self, args: Dict[str, Any], ctx=None):
        return verdict(CONTROL) if args.get("stop") else verdict(RiskTier.SAFE)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Befehl {args.get('job') or ''} {'abbrechen' if args.get('stop') else 'Status'}".strip()

    async def execute(self, db=None, job: Optional[str] = None, stop: bool = False, **_) -> ToolResult:
        if not job:
            if stop:
                return ToolResult(success=False, error="Welcher Befehl? Bitte die Job-ID angeben.")
            items = [_job_info(j, tail=False) for j in powershell.jobs() if j.background]
            return ToolResult(success=True, data={"jobs": items} if items else {"jobs": [], "note": "Keine Hintergrund-Befehle."})
        found = powershell.get(job)
        if found is None:
            return ToolResult(success=False, error=f"Keinen Befehl '{job}' gefunden.")
        if stop and found.running:
            found.kill_tree("stopped")
            await asyncio.to_thread(found._done.wait, 5)
        return ToolResult(success=True, data=_job_info(found, tail=True))
