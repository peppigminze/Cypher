from __future__ import annotations

from typing import Dict, List, Optional

from app.tools.base import Tool, ToolSecurity


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self, only_security: Optional[ToolSecurity] = None) -> List[Tool]:
        tools = list(self._tools.values())
        if only_security is not None:
            tools = [t for t in tools if t.security == only_security]
        return tools

    def schemas(self) -> List[dict]:
        return [t.schema() for t in self._tools.values()]

    def is_executable_automatically(self, name: str) -> bool:
        """Read-only and control tools run without a confirmation question
        (the per-call risk can still escalate - see Tool.risk_for)."""
        tool = self.get(name)
        return tool is not None and not tool.base_risk().needs_approval and tool.base_risk().value != "blocked"
