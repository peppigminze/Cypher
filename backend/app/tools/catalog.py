"""
Tool catalog: automatic discovery + which tools the LLM gets to see.

Discovery imports every `*_tools` module in this package; each tool class
registers itself with `@register_tool`. Adding a tool = adding a class in
such a module, nothing else.

Exposure: the model only sees the curated default set (tools with
`exposed_by_default = True` that are available with the current config,
e.g. file tools only when ALLOWED_DIRECTORIES is set) plus TOOLS_EXTRA,
minus TOOLS_DISABLED. Fewer, relevant tools measurably improve tool
choice of small local models and keep the prompt short (= faster).
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.tools.base import Tool, registered_tools
from app.tools.registry import ToolRegistry

logger = logging.getLogger("cypher.tools.catalog")

_discovered = False


def discover() -> None:
    global _discovered
    if _discovered:
        return
    import app.tools as package  # noqa: PLC0415

    for module in pkgutil.iter_modules(package.__path__):
        if module.name.endswith("_tools"):
            importlib.import_module(f"{package.__name__}.{module.name}")
    _discovered = True


def build_registry() -> ToolRegistry:
    """Registry with an instance of every discovered tool."""
    discover()
    registry = ToolRegistry()
    for name in sorted(registered_tools()):
        registry.register(registered_tools()[name]())
    return registry


def exposed_tool_names(settings: Optional[Settings] = None, registry: Optional[ToolRegistry] = None) -> List[str]:
    settings = settings or get_settings()
    registry = registry or build_registry()
    disabled = set(settings.tools_disabled_list)
    names: List[str] = []
    for tool in registry.list_tools():
        if tool.exposed_by_default and type(tool).is_available(settings) and tool.name not in disabled:
            names.append(tool.name)
    for extra in settings.tools_extra_list:
        tool = registry.get(extra)
        if tool is None:
            logger.warning("TOOLS_EXTRA names unknown tool '%s' - ignored", extra)
        elif extra not in names and extra not in disabled:
            names.append(extra)
    return names


def openai_tool_schemas(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Schemas of the exposed tools - used by eval/bench_llm.py --registry."""
    registry = build_registry()
    return [registry.get(name).openai_schema() for name in exposed_tool_names(settings, registry)]  # type: ignore[union-attr]


def describe_catalog(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Overview for the UI/diagnostics: every tool with tier and exposure."""
    settings = settings or get_settings()
    registry = build_registry()
    exposed = set(exposed_tool_names(settings, registry))
    out = []
    for tool in registry.list_tools():
        out.append(
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.base_risk().value,
                "exposed": tool.name in exposed,
                "available": type(tool).is_available(settings),
                "untrusted_output": tool.untrusted_output,
            }
        )
    return sorted(out, key=lambda t: (not t["exposed"], t["name"]))


def all_tool_classes() -> Dict[str, type[Tool]]:
    discover()
    return registered_tools()
