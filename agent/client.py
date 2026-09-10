"""
Thin HTTP client the local PC agent uses to talk to the Cypher backend.

Kept separate from the backend's own code so the agent could, in
principle, run against a remote backend over the network using only
this file plus AGENT_TOKEN - no direct DB or filesystem access to the
backend required for the sync/heartbeat parts.
"""
from __future__ import annotations

import logging
from typing import Any, List

import httpx

logger = logging.getLogger("cypher.agent.client")


class BackendClient:
    def __init__(self, base_url: str, agent_token: str, timeout_seconds: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {agent_token}"}
        self.timeout_seconds = timeout_seconds

    async def get_pending_messages(self, limit: int = 10) -> List[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(
                f"{self.base_url}/api/sync/pending",
                params={"limit": limit},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def complete_message(
        self,
        message_id: int,
        response: str,
        processed_by: str | None = None,
        observations: List[dict] | None = None,
    ) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/complete",
                json={
                    "message_id": message_id,
                    "response": response,
                    "processed_by": processed_by,
                    "observations": observations or [],
                },
                headers=self.headers,
            )
            resp.raise_for_status()

    async def fail_message(self, message_id: int, error: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/fail",
                json={"message_id": message_id, "error": error},
                headers=self.headers,
            )
            resp.raise_for_status()

    async def retry_message(
        self, message_id: int, retry_count: int, next_retry_at: str, error: str
    ) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/retry",
                json={
                    "message_id": message_id,
                    "retry_count": retry_count,
                    "next_retry_at": next_retry_at,
                    "error": error,
                },
                headers=self.headers,
            )
            resp.raise_for_status()

    async def send_heartbeat(self) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/agent/heartbeat",
                headers=self.headers,
            )
            resp.raise_for_status()

    # -------------------------------------------------------- confirmation flow

    async def create_pending_action(
        self,
        message_id: int | None,
        tool_name: str,
        arguments: dict,
        observations: List[dict],
        reply: str,
    ) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/actions",
                json={
                    "message_id": message_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "observations": observations,
                    "reply": reply,
                },
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_confirmed_actions(self, limit: int = 10) -> List[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(
                f"{self.base_url}/api/sync/confirmed-actions",
                params={"limit": limit},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def complete_action(self, action_id: int, result: Any = None) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/actions/{action_id}/complete",
                json={"result": result},
                headers=self.headers,
            )
            resp.raise_for_status()

    async def fail_action(self, action_id: int, error: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/api/sync/actions/{action_id}/fail",
                json={"error": error},
                headers=self.headers,
            )
            resp.raise_for_status()
