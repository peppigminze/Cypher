from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import DEFAULT_MEMORY_TYPE, MEMORY_TYPES
from app.memory.store import MemoryStore
from app.tools.base import RiskTier, Tool, ToolResult, register_tool


@register_tool
class SaveMemoryTool(Tool):
    name = "save_memory"
    description = (
        "Save a piece of information Cypher should remember across future "
        "conversations - a durable fact (e.g. the user's name, an ongoing "
        "project), a stated preference (e.g. 'always answer in German'), or "
        "project knowledge. Do NOT use this for information only relevant "
        "to the current message - only for things worth remembering later."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "category": {"type": "string"},
            "memory_type": {"type": "string", "enum": list(MEMORY_TYPES)},
        },
        "required": ["content"],
    }
    risk = RiskTier.WRITE
    allow_when_tainted = False

    def risk_for(self, args, ctx=None) -> RiskTier:
        # Clear rule (M2/M4): saving runs without a confirmation question only
        # when the user's own words asked for it ("merk dir ...") and no
        # untrusted content is in play; anything else needs approval.
        if ctx is not None and ctx.explicit_memory_request and not ctx.tainted:
            return RiskTier.CONTROL
        return self.base_risk()

    def describe(self, args) -> str:
        return f"Merken: „{str(args.get('content', ''))[:120]}“"

    async def execute(
        self,
        db: Session,
        content: str,
        category: str | None = None,
        memory_type: str = DEFAULT_MEMORY_TYPE,
        **_,
    ) -> ToolResult:
        store = MemoryStore(db)
        entry = store.save(content=content, category=category, memory_type=memory_type)
        return ToolResult(
            success=True, data={"id": entry.id, "content": entry.content, "memory_type": entry.memory_type}
        )


@register_tool
class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Search previously saved memories by keyword."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    risk = RiskTier.READ_ONLY

    async def execute(self, db: Session, query: str, limit: int = 5, **_) -> ToolResult:
        store = MemoryStore(db)
        results = store.search(query, limit=limit)
        data = [
            {"id": m.id, "content": m.content, "category": m.category, "memory_type": m.memory_type}
            for m in results
        ]
        return ToolResult(success=True, data=data)
