"""
Tool execution + OpenAI-format schema conversion for native LLM
function-calling (see app/agent/native_tool_agent.py).

Loosely inspired by the personalJarvis reference project's idea of a
single, central executor that every tool call must go through (there:
a Tool protocol with name/schema/execute, run exclusively via a
ToolExecutor so safety/logging can't be bypassed by calling a tool
directly). Adapted here to this project's existing Tool/ToolRegistry
model instead of copying personalJarvis's plugin/registry machinery -
this is just the "one authorized execution path + always log" part.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.tools.base import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger("cypher.tools.executor")


def to_openai_tools(registry: ToolRegistry, names: List[str]) -> List[Dict[str, Any]]:
    """Build the OpenAI-format `tools` array (the shape both the OpenAI
    API and Ollama's OpenAI-compat endpoint expect) for the given tool
    names. Unknown names are silently skipped so a typo in the caller's
    tool_names list doesn't crash the request."""
    schemas: List[Dict[str, Any]] = []
    for name in names:
        tool = registry.get(name)
        if tool is None:
            logger.warning("to_openai_tools: no such tool '%s' - skipped", name)
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            }
        )
    return schemas


class ToolExecutor:
    """The single authorized path for running a tool the LLM picked.

    Every call is logged to the console (name, arguments, result) here
    and only here, so tool usage is always visible regardless of which
    caller (chat loop, test script, ...) triggered it. Bad arguments or
    an unexpected exception inside a tool become a normal failed
    ToolResult instead of raising into - and killing - the chat loop.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, name: str, arguments: Dict[str, Any], db=None) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            result = ToolResult(success=False, error=f"Unknown tool '{name}'.")
        else:
            try:
                result = await tool.execute(db=db, **arguments)
            except TypeError as exc:
                result = ToolResult(success=False, error=f"Invalid arguments for tool '{name}': {exc}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Tool '%s' raised an unexpected error", name)
                result = ToolResult(success=False, error=f"Tool '{name}' failed: {exc}")

        logger.info(
            "TOOL CALL -> name=%s arguments=%s | success=%s data=%s error=%s",
            name,
            arguments,
            result.success,
            result.data,
            result.error,
        )
        return result

    @staticmethod
    def parse_arguments(raw_arguments: str) -> Dict[str, Any]:
        """Ollama/OpenAI send tool-call arguments as a JSON *string*, not
        a dict - parse defensively since a confused model can emit
        malformed JSON (empty string, trailing comma, ...)."""
        if not raw_arguments:
            return {}
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            logger.warning("Could not parse tool call arguments as JSON: %r", raw_arguments)
            return {}
        return parsed if isinstance(parsed, dict) else {}
