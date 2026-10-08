"""
Memory tools: "Merk dir ...", "Vergiss ...", "Was weisst du über ...?".

All three go through app/memory/service.py (core memory + FTS5 archive).
Risk rules:
  remember   runs without a question only when the user's own words asked
             for it ("merk dir") and no untrusted content is in the turn;
             otherwise it needs approval. Never in a tainted turn.
  forget     always shows WHICH entries it would delete and asks first -
             deleting is not undoable. Never in a tainted turn.
  search     read-only.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from app.memory.service import MAX_MEMORY_CHARS, get_memory
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool


def _short(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RememberArgs(ArgsModel):
    fact: str = Field(..., min_length=1, max_length=MAX_MEMORY_CHARS, description="Der Fakt, kurz und in der dritten Person, z. B. 'Silvan hat freitags Berufsschule'")


@register_tool
class RememberTool(Tool):
    name = "remember"
    description = (
        "Etwas dauerhaft merken, wenn der Nutzer es verlangt ('merk dir ...', 'vergiss nicht, dass ...'): "
        "Fakten über ihn, Vorlieben, Projekte. Nicht für einmalige Infos aus dem aktuellen Gespräch."
    )
    Args = RememberArgs
    risk = RiskTier.WRITE
    allow_when_tainted = False
    exposed_by_default = True

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        if ctx is not None and ctx.explicit_memory_request and not ctx.tainted:
            return RiskTier.CONTROL
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Merken: „{_short(str(args.get('fact', '')), 120)}“"

    async def execute(self, db=None, fact: str = "", **_) -> ToolResult:
        try:
            item = get_memory().remember(fact, db=db, source="user")
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"remembered": item.text, "where": "Kern-Gedächtnis" if item.layer == "core" else "Archiv"})


class ForgetArgs(ArgsModel):
    query: str = Field(..., min_length=2, max_length=200, description="Worum es geht, z. B. 'Kaffee'")


@register_tool
class ForgetTool(Tool):
    name = "forget"
    description = "Einen gemerkten Fakt wieder vergessen/löschen ('vergiss, dass ...'). Fragt vorher nach."
    Args = ForgetArgs
    risk = RiskTier.WRITE
    allow_when_tainted = False
    exposed_by_default = True

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        # Nothing matches -> nothing would be deleted: no approval card for
        # nothing, the tool just reports that it found nothing.
        if not get_memory().forget_candidates(str(args.get("query", ""))):
            return RiskTier.READ_ONLY
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        found = get_memory().forget_candidates(str(args.get("query", "")))
        if not found:
            return f"Vergessen: nichts gefunden zu „{_short(str(args.get('query', '')))}“"
        more = f" (+{len(found) - 1} weitere)" if len(found) > 1 else ""
        return f"Vergessen: „{_short(found[0].text)}“{more}"

    async def execute(self, db=None, query: str = "", **_) -> ToolResult:
        memory = get_memory()
        found = memory.forget_candidates(query, db=db)
        if not found:
            return ToolResult(success=False, error=f"Zu „{query}“ ist nichts gespeichert.")
        deleted = [item.text for item in found if memory.delete(item.layer, item.id, db=db)]
        return ToolResult(success=True, data={"forgotten": deleted})


class SearchMemoryArgs(ArgsModel):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(5, ge=1, le=20)


@register_tool
class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Im Gedächtnis nach früher gemerkten Fakten suchen ('was hab ich dir über ... erzählt?')."
    Args = SearchMemoryArgs
    risk = RiskTier.READ_ONLY
    exposed_by_default = True

    async def execute(self, db=None, query: str = "", limit: int = 5, **_) -> ToolResult:
        items = get_memory().find(query, db=db, limit=limit)
        return ToolResult(success=True, data={"count": len(items), "entries": [item.text for item in items]})


class SaveMemoryArgs(ArgsModel):
    content: str = Field(..., min_length=1, max_length=4000)
    category: Optional[str] = None
    memory_type: Optional[str] = None


@register_tool
class SaveMemoryTool(Tool):
    """Deprecated name of `remember` (old agent pipeline, M5 removes it)."""

    name = "save_memory"
    description = "Veraltet - bitte 'remember' benutzen. Speichert einen Fakt im Gedächtnis."
    Args = SaveMemoryArgs
    risk = RiskTier.WRITE
    allow_when_tainted = False
    exposed_by_default = False

    def risk_for(self, args: Dict[str, Any], ctx=None) -> RiskTier:
        if ctx is not None and ctx.explicit_memory_request and not ctx.tainted:
            return RiskTier.CONTROL
        return self.base_risk()

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Merken: „{_short(str(args.get('content', '')), 120)}“"

    async def execute(self, db=None, content: str = "", **_) -> ToolResult:
        try:
            item = get_memory().remember(content, db=db, source="user")
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=True, data={"id": item.id, "content": item.text, "layer": item.layer})
