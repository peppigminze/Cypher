"""
Archival memory: everything worth remembering that is not in the small
core memory (app/memory/core.py) - searchable, unlimited, in SQLite.

Search uses SQLite's built-in full-text index FTS5 (no extra dependency,
no embeddings): ranked by BM25, accents/umlauts folded, prefix matching so
"Berufsschule" also finds "Berufsschul-Stundenplan". The index is an
"external content" table: the rows live in memory_entries as before, FTS5
only stores the index, and triggers keep both in sync. If FTS5 is missing
(very old SQLite), search falls back to the old LIKE match.

The MemoryStore interface (save / search / get / update / delete) is what
callers use; an embeddings backend could replace it later.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database.models import DEFAULT_MEMORY_TYPE, MEMORY_TYPES, MemoryEntry

logger = logging.getLogger("cypher.memory.store")

_FTS_SETUP = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
           content, category, content='memory_entries', content_rowid='id',
           tokenize='unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_ai AFTER INSERT ON memory_entries BEGIN
           INSERT INTO memory_fts(rowid, content, category) VALUES (new.id, new.content, new.category);
       END""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_ad AFTER DELETE ON memory_entries BEGIN
           INSERT INTO memory_fts(memory_fts, rowid, content, category) VALUES ('delete', old.id, old.content, old.category);
       END""",
    """CREATE TRIGGER IF NOT EXISTS memory_fts_au AFTER UPDATE ON memory_entries BEGIN
           INSERT INTO memory_fts(memory_fts, rowid, content, category) VALUES ('delete', old.id, old.content, old.category);
           INSERT INTO memory_fts(rowid, content, category) VALUES (new.id, new.content, new.category);
       END""",
]

# Words that carry no meaning for a memory lookup.
_STOPWORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und", "oder", "aber",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mir", "mich", "dir", "dich", "mein", "meine", "meinen",
    "dein", "deine", "ist", "bin", "bist", "sind", "war", "hat", "habe", "hast", "haben", "wird", "werde",
    "was", "wie", "wo", "wann", "wer", "warum", "welche", "welcher", "welches", "noch", "schon", "auch",
    "nicht", "kein", "keine", "mit", "von", "zu", "zum", "zur", "auf", "aus", "bei", "für", "fuer", "im",
    "in", "an", "am", "um", "über", "dass", "ob", "wenn", "so", "da", "denn", "mal", "bitte", "eigentlich",
    "weisst", "weißt", "kannst", "sag", "merk", "merke", "vergiss", "cypher", "hey",
}

# Per database engine: is the FTS5 index set up? (Tests use their own engines.)
_fts_ready: Dict[int, bool] = {}


def ensure_fts(engine: Engine) -> bool:
    """Create the FTS5 index + triggers (idempotent) and fill it once."""
    if engine.dialect.name != "sqlite":
        _fts_ready[id(engine)] = False
        return False
    try:
        with engine.begin() as conn:
            existed = conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'")).first() is not None
            for statement in _FTS_SETUP:
                conn.execute(text(statement))
            if not existed:
                conn.execute(text("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')"))
        _fts_ready[id(engine)] = True
    except Exception as exc:  # noqa: BLE001 - FTS5 missing: keyword fallback
        logger.warning("SQLite FTS5 not available (%s) - memory search falls back to LIKE", exc)
        _fts_ready[id(engine)] = False
    return _fts_ready[id(engine)]


def query_terms(query: str) -> List[str]:
    words = re.findall(r"[\wäöüÄÖÜß]+", (query or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS and not w.isdigit()][:12]


def fts_expression(terms: Sequence[str]) -> str:
    """User text -> safe FTS5 query: every term quoted (so nothing in it is
    FTS syntax), longer words as prefix ("berufsschul"*), joined with OR."""
    parts = []
    for term in terms:
        stem = term[:-2] if len(term) > 6 else term  # crude German stemming: "schule" ~ "schulen"
        parts.append(f'"{stem}"*' if len(stem) >= 4 else f'"{stem}"')
    return " OR ".join(parts)


class MemoryStore:
    def __init__(self, db: Session):
        self.db = db

    def save(self, content: str, category: Optional[str] = None, memory_type: str = DEFAULT_MEMORY_TYPE) -> MemoryEntry:
        if memory_type not in MEMORY_TYPES:
            memory_type = DEFAULT_MEMORY_TYPE
        entry = MemoryEntry(content=content, category=category, memory_type=memory_type)
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get(self, memory_id: int) -> Optional[MemoryEntry]:
        return self.db.get(MemoryEntry, memory_id)

    def update(self, memory_id: int, content: str) -> Optional[MemoryEntry]:
        entry = self.get(memory_id)
        if entry is None:
            return None
        entry.content = content
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def search_ranked(self, query: str, limit: int = 5) -> List[Tuple[MemoryEntry, float]]:
        """Best matches with a relevance score (higher = better)."""
        terms = query_terms(query)
        if not terms:
            return []
        bind = self.db.get_bind()
        ready = _fts_ready.get(id(bind))
        if ready is None:
            ready = ensure_fts(bind)
        if ready:
            rows = self.db.execute(
                text("SELECT rowid, bm25(memory_fts) AS rank FROM memory_fts WHERE memory_fts MATCH :q ORDER BY rank LIMIT :n"),
                {"q": fts_expression(terms), "n": limit},
            ).all()
            out = []
            for rowid, rank in rows:
                entry = self.get(int(rowid))
                if entry is not None:
                    out.append((entry, -float(rank)))  # bm25: lower is better
            return out
        conditions = [MemoryEntry.content.ilike(f"%{t}%") for t in terms]
        stmt = select(MemoryEntry).where(or_(*conditions)).order_by(MemoryEntry.updated_at.desc()).limit(limit)
        return [(e, 1.0) for e in self.db.execute(stmt).scalars().all()]

    def search(self, query: str, limit: int = 5) -> Sequence[MemoryEntry]:
        if not (query or "").strip():
            return self.list_recent(limit=limit)
        return [entry for entry, _ in self.search_ranked(query, limit)]

    def list_recent(self, limit: int = 5) -> Sequence[MemoryEntry]:
        stmt = select(MemoryEntry).order_by(MemoryEntry.updated_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def list_all(self) -> Sequence[MemoryEntry]:
        stmt = select(MemoryEntry).order_by(MemoryEntry.updated_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def delete(self, memory_id: int) -> bool:
        entry = self.get(memory_id)
        if entry is None:
            return False
        self.db.delete(entry)
        self.db.commit()
        return True
