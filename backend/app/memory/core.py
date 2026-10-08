"""
Core memory: the few facts Cypher always knows ("Silvan ist 17 und macht
eine IT-Lehre", "Freitags ist Berufsschule").

Stored as human-readable JSON in DATA_DIR/core_memory.json - you can open,
edit or back it up with any editor. Every fact goes into the system prompt,
so the total is capped by a token budget (MEMORY_CORE_MAX_TOKENS): when it's
full, new facts go to the searchable archive instead (app/memory/store.py).

Writes are atomic (temp file + rename): a crash mid-write can't leave a
half-written file that loses everything.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("cypher.memory.core")

FILE_NAME = "core_memory.json"
FILE_VERSION = 1
MAX_FACT_CHARS = 300


def estimate_tokens(text: str) -> int:
    # Same rule of thumb as the conversation window (~3.5 chars per token).
    return int(len(text) / 3.5) + 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"[^\wäöüß]+", " ", text.lower()).strip()


@dataclass
class CoreFact:
    id: str
    text: str
    created_at: str
    updated_at: str
    source: str = "user"  # user | ui | migration


class CoreMemoryFull(Exception):
    """The fact doesn't fit into the token budget."""


class CoreMemory:
    def __init__(self, path: Path, max_tokens: int) -> None:
        self.path = path
        self.max_tokens = max_tokens
        self._lock = threading.Lock()
        self._facts: Optional[List[CoreFact]] = None

    # ------------------------------------------------------------ persistence
    def _load(self) -> List[CoreFact]:
        if self._facts is not None:
            return self._facts
        facts: List[CoreFact] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for item in raw.get("facts", []):
                    text = str(item.get("text", "")).strip()
                    if text:
                        facts.append(CoreFact(
                            id=str(item.get("id") or _new_id()), text=text[:MAX_FACT_CHARS],
                            created_at=str(item.get("created_at") or _now()), updated_at=str(item.get("updated_at") or _now()),
                            source=str(item.get("source") or "user"),
                        ))
            except (OSError, ValueError) as exc:
                # Don't silently start empty and overwrite the user's file later.
                backup = self.path.with_suffix(f".broken-{datetime.now():%Y%m%d-%H%M%S}.json")
                logger.error("core_memory.json unreadable (%s) - kept as %s, starting empty", exc, backup.name)
                try:
                    self.path.replace(backup)
                except OSError:
                    pass
        self._facts = facts
        return facts

    def _save(self, facts: List[CoreFact]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": FILE_VERSION, "facts": [asdict(f) for f in facts]}
        fd, tmp = tempfile.mkstemp(prefix=".core_memory.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._facts = facts

    # ------------------------------------------------------------ API
    def facts(self) -> List[CoreFact]:
        with self._lock:
            return list(self._load())

    def used_tokens(self, facts: Optional[List[CoreFact]] = None) -> int:
        return sum(estimate_tokens(f.text) for f in (facts if facts is not None else self.facts()))

    def find_duplicate(self, text: str) -> Optional[CoreFact]:
        wanted = _norm(text)
        return next((f for f in self.facts() if _norm(f.text) == wanted), None)

    def add(self, text: str, source: str = "user") -> CoreFact:
        text = " ".join(text.split())[:MAX_FACT_CHARS]
        if not text:
            raise ValueError("empty fact")
        with self._lock:
            facts = list(self._load())
            for fact in facts:
                if _norm(fact.text) == _norm(text):
                    return fact  # already known - no duplicate
            if self.used_tokens(facts) + estimate_tokens(text) > self.max_tokens:
                raise CoreMemoryFull(text)
            fact = CoreFact(id=_new_id(), text=text, created_at=_now(), updated_at=_now(), source=source)
            self._save(facts + [fact])
            return fact

    def update(self, fact_id: str, text: str) -> Optional[CoreFact]:
        text = " ".join(text.split())[:MAX_FACT_CHARS]
        if not text:
            raise ValueError("empty fact")
        with self._lock:
            facts = list(self._load())
            for i, fact in enumerate(facts):
                if fact.id == fact_id:
                    others = facts[:i] + facts[i + 1 :]
                    if self.used_tokens(others) + estimate_tokens(text) > self.max_tokens:
                        raise CoreMemoryFull(text)
                    updated = CoreFact(fact.id, text, fact.created_at, _now(), fact.source)
                    facts[i] = updated
                    self._save(facts)
                    return updated
            return None

    def delete(self, fact_id: str) -> Optional[CoreFact]:
        with self._lock:
            facts = list(self._load())
            for fact in facts:
                if fact.id == fact_id:
                    self._save([f for f in facts if f.id != fact_id])
                    return fact
            return None

    def reload(self) -> None:
        """Forget the cached copy (e.g. after editing the file by hand)."""
        with self._lock:
            self._facts = None


def _new_id() -> str:
    return "c" + uuid.uuid4().hex[:8]
