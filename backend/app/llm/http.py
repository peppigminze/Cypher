"""
Shared, pooled httpx clients.

Opening a new AsyncClient per LLM call (as the old providers did) throws
away keep-alive connections and costs a TCP handshake per request. One
client per (key, event loop) is reused instead; the loop check matters
because tests (and uvicorn's reload) run several event loops in one
process, and a client must never be used from a loop it wasn't created in.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Dict, Tuple

import httpx

_clients: Dict[str, Tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}


def shared_client(key: str, factory: Callable[[], httpx.AsyncClient]) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    entry = _clients.get(key)
    if entry is not None:
        owner_loop, client = entry
        if owner_loop is loop and not client.is_closed:
            return client
    client = factory()
    _clients[key] = (loop, client)
    return client


async def aclose_all() -> None:
    loop = asyncio.get_running_loop()
    for key, (owner_loop, client) in list(_clients.items()):
        if owner_loop is loop:
            await client.aclose()
        _clients.pop(key, None)
