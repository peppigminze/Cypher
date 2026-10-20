"""
Memory API (M4).

Unchanged for existing clients:
    GET    /api/memory?q=&memory_type=   archive entries (list)
    POST   /api/memory                    new archive entry
    DELETE /api/memory/{id}               delete an archive entry

New - both layers, for the memory editor in the UI:
    GET    /api/memory/overview           core facts + archive + core budget
    POST   /api/memory/items              {"text", "layer": "core"|"archive"}
    PATCH  /api/memory/items/{layer}/{id} {"text"} edit, or {"move_to"} pin/unpin
    DELETE /api/memory/items/{layer}/{id}

    GET    /api/conversations             stored conversation history (summary)
    DELETE /api/conversations             delete all stored conversation history
"""
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database.db import get_db
from app.database.models import ConversationHistory
from app.memory.core import CoreMemoryFull
from app.memory.service import MAX_MEMORY_CHARS, get_memory
from app.memory.store import MemoryStore
from app.schemas import MemoryCreate, MemoryOut

router = APIRouter(prefix="/api/memory", tags=["memory"])
conversations_router = APIRouter(prefix="/api/conversations", tags=["memory"])

Layer = Literal["core", "archive"]

_FULL = "Das Kern-Gedächtnis ist voll. Lösch dort etwas oder leg den Eintrag ins Archiv."


@router.get("", response_model=List[MemoryOut])
def list_memory(
    db: Session = Depends(get_db),
    _user=Depends(require_user),
    q: str | None = None,
    memory_type: str | None = None,
) -> list:
    store = MemoryStore(db)
    entries = list(store.search(q, limit=50)) if q else list(store.list_all())
    if memory_type:
        entries = [e for e in entries if e.memory_type == memory_type]
    return entries


@router.post("", response_model=MemoryOut)
def create_memory(payload: MemoryCreate, db: Session = Depends(get_db), _user=Depends(require_user)):
    return MemoryStore(db).save(content=payload.content, category=payload.category, memory_type=payload.memory_type)


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db), _user=Depends(require_user)) -> dict:
    if not get_memory().delete("archive", str(memory_id), db=db):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden.")
    return {"id": memory_id, "deleted": True}


# ------------------------------------------------------------------ both layers
class MemoryItemOut(BaseModel):
    layer: Layer
    id: str
    text: str


class MemoryOverview(BaseModel):
    core: List[MemoryItemOut]
    archive: List[MemoryItemOut]
    core_used_tokens: int
    core_max_tokens: int


class MemoryItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)
    layer: Layer = "core"


class MemoryItemPatch(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=MAX_MEMORY_CHARS)
    move_to: Optional[Layer] = None


def _out(item) -> MemoryItemOut:
    return MemoryItemOut(layer=item.layer, id=item.id, text=item.text)


@router.get("/overview", response_model=MemoryOverview)
def overview(db: Session = Depends(get_db), _user=Depends(require_user)) -> MemoryOverview:
    memory = get_memory()
    items = memory.list_all(db=db)
    budget = memory.budget()
    return MemoryOverview(
        core=[_out(i) for i in items if i.layer == "core"],
        archive=[_out(i) for i in items if i.layer == "archive"],
        core_used_tokens=budget["used_tokens"],
        core_max_tokens=budget["max_tokens"],
    )


@router.post("/items", response_model=MemoryItemOut)
def add_item(payload: MemoryItemIn, db: Session = Depends(get_db), _user=Depends(require_user)) -> MemoryItemOut:
    memory = get_memory()
    if payload.layer == "core":
        try:
            fact = memory.core.add(payload.text, source="ui")
        except CoreMemoryFull:
            raise HTTPException(status_code=409, detail=_FULL)
        except ValueError:
            raise HTTPException(status_code=422, detail="Leerer Eintrag.")
        return MemoryItemOut(layer="core", id=fact.id, text=fact.text)
    entry = MemoryStore(db).save(payload.text, category="ui")
    return MemoryItemOut(layer="archive", id=str(entry.id), text=entry.content)


@router.patch("/items/{layer}/{item_id}", response_model=MemoryItemOut)
def patch_item(layer: Layer, item_id: str, payload: MemoryItemPatch, db: Session = Depends(get_db), _user=Depends(require_user)) -> MemoryItemOut:
    memory = get_memory()
    try:
        if payload.move_to is not None:
            item = memory.move(layer, item_id, payload.move_to, db=db)
        elif payload.text is not None:
            item = memory.update(layer, item_id, payload.text, db=db)
        else:
            raise HTTPException(status_code=422, detail="Nichts zu ändern.")
    except CoreMemoryFull:
        raise HTTPException(status_code=409, detail=_FULL)
    if item is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden.")
    return _out(item)


@router.delete("/items/{layer}/{item_id}")
def delete_item(layer: Layer, item_id: str, db: Session = Depends(get_db), _user=Depends(require_user)) -> dict:
    if not get_memory().delete(layer, item_id, db=db):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden.")
    return {"layer": layer, "id": item_id, "deleted": True}


# ------------------------------------------------------------------ conversation history
@conversations_router.get("")
def list_conversations(db: Session = Depends(get_db), _user=Depends(require_user)) -> dict:
    rows = db.query(ConversationHistory).order_by(ConversationHistory.updated_at.desc()).limit(50).all()
    return {"conversations": [{"id": r.id, "messages": r.message_count, "updated_at": r.updated_at.isoformat()} for r in rows]}


@conversations_router.delete("")
def delete_conversations(_user=Depends(require_user)) -> dict:
    from app.agent.engine import conversations  # noqa: PLC0415

    conversations.flush()
    return {"deleted": conversations.delete_all()}
