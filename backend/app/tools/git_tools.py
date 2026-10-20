"""
Git tools (project spec section 27). Read-only operations (status,
diff, log, branch) are SAFE - they can't change anything. Anything that
changes repository state (commit, push, pull - a pull can rewrite the
working tree via a merge) is CONFIRM_REQUIRED, consistent with how
every other state-changing tool in this project is gated (project spec
section 14), even though the spec's own explicit "needs confirmation"
examples only name git_push. All commands run inside a repo path
resolved through ALLOWED_DIRECTORIES (app/tools/paths.py) - same
sandbox as the file/terminal tools.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session

from app.tools.base import RiskTier, Tool, ToolResult, register_tool
from app.tools.paths import PathNotAllowedError, resolve_allowed_path

GIT_TIMEOUT_SECONDS = 20
MAX_OUTPUT_CHARS = 8_000


async def _run_git(repo_path: str, args: list[str]) -> ToolResult:
    try:
        repo = resolve_allowed_path(repo_path)
    except PathNotAllowedError as exc:
        return ToolResult(success=False, error=str(exc))
    if not repo.is_dir():
        return ToolResult(success=False, error=f"'{repo}' is not a directory.")

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=GIT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, error=f"git {' '.join(args)} timed out.")
    except OSError as exc:
        return ToolResult(success=False, error=f"Could not run git: {exc}")

    stdout = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    stderr = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

    if proc.returncode != 0:
        return ToolResult(success=False, error=stderr.strip() or f"git {' '.join(args)} failed.")
    return ToolResult(success=True, data={"stdout": stdout.strip()})


@register_tool
class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the working tree status of a git repository (like 'git status')."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    risk = RiskTier.READ_ONLY
    untrusted_output = True

    async def execute(self, db: Session | None = None, path: str = ".", **_) -> ToolResult:
        return await _run_git(path, ["status"])


@register_tool
class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show uncommitted changes in a git repository (like 'git diff'). Set staged=true for 'git diff --staged'."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "staged": {"type": "boolean"}},
        "required": ["path"],
    }
    risk = RiskTier.READ_ONLY
    untrusted_output = True

    async def execute(self, db: Session | None = None, path: str = ".", staged: bool = False, **_) -> ToolResult:
        args = ["diff", "--staged"] if staged else ["diff"]
        return await _run_git(path, args)


@register_tool
class GitLogTool(Tool):
    name = "git_log"
    description = "Show recent commit history of a git repository (like 'git log --oneline')."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"],
    }
    risk = RiskTier.READ_ONLY
    untrusted_output = True

    async def execute(self, db: Session | None = None, path: str = ".", limit: int = 10, **_) -> ToolResult:
        return await _run_git(path, ["log", f"-{max(1, min(limit, 100))}", "--oneline"])


@register_tool
class GitBranchTool(Tool):
    name = "git_branch"
    description = "List branches of a git repository and show which one is checked out."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    risk = RiskTier.READ_ONLY

    async def execute(self, db: Session | None = None, path: str = ".", **_) -> ToolResult:
        return await _run_git(path, ["branch"])


@register_tool
class GitCommitTool(Tool):
    name = "git_commit"
    description = "Commit changes in a git repository with a message. Requires confirmation."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "message": {"type": "string"},
            "add_all": {"type": "boolean", "description": "Stage all changes before committing."},
        },
        "required": ["path", "message"],
    }
    risk = RiskTier.WRITE

    async def execute(
        self, db: Session | None = None, path: str = ".", message: str = "", add_all: bool = False, **_
    ) -> ToolResult:
        if not message.strip():
            return ToolResult(success=False, error="Commit message must not be empty.")
        if add_all:
            add_result = await _run_git(path, ["add", "-A"])
            if not add_result.success:
                return add_result
        return await _run_git(path, ["commit", "-m", message])


@register_tool
class GitPushTool(Tool):
    name = "git_push"
    description = "Push committed changes to the remote. Requires confirmation."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    risk = RiskTier.DESTRUCTIVE

    async def execute(self, db: Session | None = None, path: str = ".", **_) -> ToolResult:
        return await _run_git(path, ["push"])


@register_tool
class GitPullTool(Tool):
    name = "git_pull"
    description = "Pull changes from the remote (can modify the working tree via a merge). Requires confirmation."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    risk = RiskTier.DESTRUCTIVE

    async def execute(self, db: Session | None = None, path: str = ".", **_) -> ToolResult:
        return await _run_git(path, ["pull"])
