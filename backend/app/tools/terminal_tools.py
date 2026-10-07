"""
Terminal access (project spec sections 13, 15). This is the single most
powerful tool in Cypher, so it layers every safety mechanism the
project has:

    1. security = CONFIRM_REQUIRED - a human must approve every single
       invocation via the PWA (app/agent/core.py never runs it
       automatically, see the confirmation flow from Phase 2).
    2. command_safety.is_blocked_command() - a hard denylist for a
       handful of unambiguously catastrophic patterns, refused before
       it even reaches confirmation.
    3. The working directory is resolved through resolve_allowed_path()
       (app/tools/paths.py) - it must be inside ALLOWED_DIRECTORIES.
    4. A hard timeout (TERMINAL_TIMEOUT_SECONDS) - a hung command can't
       block the agent forever.
    5. Every invocation is logged to command_logs (CommandLog) with
       full stdout/stderr/exit code, regardless of outcome.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import CommandLog
from app.tools.base import RiskTier, Tool, ToolResult, register_tool
from app.tools.command_safety import is_blocked_command
from app.tools.paths import PathNotAllowedError, resolve_allowed_path

logger = logging.getLogger("cypher.tools.terminal")

MAX_OUTPUT_CHARS = 20_000


@register_tool
class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command on the user's PC inside an allowed working directory. "
        "Always requires human confirmation. Obviously destructive commands "
        "(formatting a drive, wiping a whole filesystem, shutting the machine down) "
        "are refused outright and never reach confirmation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "working_directory": {"type": "string"},
        },
        "required": ["command"],
    }
    risk = RiskTier.DESTRUCTIVE

    async def execute(self, db: Session, command: str, working_directory: str = ".", **_) -> ToolResult:
        blocked_reason = is_blocked_command(command)
        if blocked_reason:
            logger.warning("Refused blocked command (%s): %s", blocked_reason, command)
            return ToolResult(success=False, error=f"Refused - matches a blocked pattern ({blocked_reason}).")

        try:
            cwd = resolve_allowed_path(working_directory)
        except PathNotAllowedError as exc:
            return ToolResult(success=False, error=str(exc))
        if not cwd.is_dir():
            return ToolResult(success=False, error=f"Working directory '{cwd}' does not exist.")

        settings = get_settings()
        started_at = datetime.now(timezone.utc)
        timed_out = False
        exit_code: int | None = None
        stdout = ""
        stderr = ""

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=settings.TERMINAL_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout_bytes, stderr_bytes = await proc.communicate()
            exit_code = proc.returncode
            stdout = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        except OSError as exc:
            stderr = str(exc)

        finished_at = datetime.now(timezone.utc)
        db.add(
            CommandLog(
                command=command,
                working_dir=str(cwd),
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        db.commit()

        if timed_out:
            return ToolResult(
                success=False, error=f"Command timed out after {settings.TERMINAL_TIMEOUT_SECONDS:.0f}s."
            )
        if exit_code is None:
            return ToolResult(success=False, error=stderr or "Could not start command.")

        return ToolResult(
            success=(exit_code == 0),
            data={"exit_code": exit_code, "stdout": stdout, "stderr": stderr},
            error=None if exit_code == 0 else f"Command exited with code {exit_code}.",
        )
