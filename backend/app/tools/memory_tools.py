from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import DEFAULT_MEMORY_TYPE, MEMORY_TYPES
from app.memory.store import MemoryStore
from app.tools.base import Tool, ToolResult, ToolSecurity


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
    security = ToolSecurity.SAFE

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


class SearchMemoryTool(Tool):
    name = "search_memory"
    description = "Search previously saved memories by keyword."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    security = ToolSecurity.SAFE

    async def execute(self, db: Session, query: str, limit: int = 5, **_) -> ToolResult:
        store = MemoryStore(db)
        results = store.search(query, limit=limit)
        data = [
            {"id": m.id, "content": m.content, "category": m.category, "memory_type": m.memory_type}
            for m in results
        ]
        return ToolResult(success=True, data=data)
