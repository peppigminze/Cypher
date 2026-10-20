"""Backward-compatible entry point: the full registry of every tool.

Tools are discovered automatically (app/tools/catalog.py); there is no
hand-maintained list here any more.
"""
from __future__ import annotations

from app.tools.catalog import build_registry
from app.tools.registry import ToolRegistry


def build_default_registry() -> ToolRegistry:
    return build_registry()
