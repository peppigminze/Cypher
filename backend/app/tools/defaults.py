from __future__ import annotations

from app.tools.app_tools import CloseApplicationTool, ListRunningApplicationsTool, OpenApplicationTool
from app.tools.file_tools import (
    CopyFileTool,
    DeleteFileTool,
    ListFilesTool,
    MoveFileTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)
from app.tools.git_tools import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPullTool,
    GitPushTool,
    GitStatusTool,
)
from app.tools.memory_tools import SaveMemoryTool, SearchMemoryTool
from app.tools.project_tools import ListProjectsTool
from app.tools.registry import ToolRegistry
from app.tools.system_tools import (
    CpuUsageTool,
    DiskUsageTool,
    GetCurrentTimeTool,
    GetSystemStatusTool,
    NetworkStatusTool,
    RamUsageTool,
)
from app.tools.task_tools import CompleteTaskTool, CreateTaskTool, DeleteTaskTool, ListTasksTool
from app.tools.terminal_tools import RunCommandTool
from app.tools.web_tools import FetchUrlTool, WebSearchTool


def build_default_registry() -> ToolRegistry:
    """Tools with security = SAFE run automatically; CONFIRM_REQUIRED
    tools always pause for human approval (project spec sections 12-14).
    PC-control tools (files, terminal, applications) additionally only
    ever touch ALLOWED_DIRECTORIES (app/tools/paths.py) - empty by
    default, so they do nothing until the user opts a directory in."""
    registry = ToolRegistry()

    # Tasks / memory / basic info - SAFE
    registry.register(CreateTaskTool())
    registry.register(ListTasksTool())
    registry.register(CompleteTaskTool())
    registry.register(SaveMemoryTool())
    registry.register(SearchMemoryTool())
    registry.register(GetCurrentTimeTool())
    registry.register(GetSystemStatusTool())
    registry.register(CpuUsageTool())
    registry.register(RamUsageTool())
    registry.register(DiskUsageTool())
    registry.register(NetworkStatusTool())
    registry.register(ListProjectsTool())
    registry.register(FetchUrlTool())
    registry.register(WebSearchTool())

    # Destructive / task-affecting - CONFIRM_REQUIRED
    registry.register(DeleteTaskTool())

    # Files - read-only SAFE, mutating CONFIRM_REQUIRED
    registry.register(ListFilesTool())
    registry.register(ReadFileTool())
    registry.register(SearchFilesTool())
    registry.register(WriteFileTool())
    registry.register(MoveFileTool())
    registry.register(CopyFileTool())
    registry.register(DeleteFileTool())

    # Applications - listing is SAFE, opening/closing is CONFIRM_REQUIRED
    registry.register(ListRunningApplicationsTool())
    registry.register(OpenApplicationTool())
    registry.register(CloseApplicationTool())

    # Terminal - always CONFIRM_REQUIRED, plus a hard denylist
    registry.register(RunCommandTool())

    # Git - read-only SAFE, anything changing repo state CONFIRM_REQUIRED
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    registry.register(GitLogTool())
    registry.register(GitBranchTool())
    registry.register(GitCommitTool())
    registry.register(GitPushTool())
    registry.register(GitPullTool())

    return registry
