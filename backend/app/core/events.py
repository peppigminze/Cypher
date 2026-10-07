"""
Tiny in-process publish/subscribe bus.

Decouples producers (reminder scheduler, approval store, tool executor)
from consumers (voice sessions over WebSocket, SSE streams) without any
of them importing each other. Each subscriber gets its own bounded queue;
a slow subscriber drops its oldest events instead of blocking publishers.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Set

logger = logging.getLogger("cypher.events")

_QUEUE_SIZE = 100


@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event_type: str, **data: Any) -> None:
        """Safe to call from any thread (e.g. the scheduler's DB thread)."""
        event = Event(event_type, data)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is None and self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._deliver, event)
        else:
            self._deliver(event)

    def _deliver(self, event: Event) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[Event]:
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
