"""
Sync worker for the local PC agent.

Two things are polled every cycle (see project spec sections 3, 14, 19):

    1. GET /api/sync/pending
        -> for each new message: run the Cypher Agent Core pipeline
           locally (local LLM + local tools + local DB).
           - finished            -> POST /api/sync/complete
           - paused (needs a     -> POST /api/sync/actions (PendingAction),
             human confirmation)    message stays 'processing' until resolved
           - local LLM down      -> POST /api/sync/retry (requeued with
                                     backoff, up to MESSAGE_MAX_RETRIES -
                                     see _retry_or_fail() - before finally
                                     POST /api/sync/fail)

    2. GET /api/sync/confirmed-actions
        -> for each action a human just approved via the PWA: execute
           the tool locally, then resume the paused pipeline with the
           prior observations.
           - finished            -> POST /api/sync/complete
           - paused again        -> POST /api/sync/actions (chained confirmation)

This process is what actually "does the work" while the PWA/backend
may be reachable from anywhere. If the PC is off, nothing here runs -
messages simply stay 'pending' (or a PendingAction stays
'awaiting_confirmation'/'confirmed') until this worker comes back
online and polls again.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The agent reuses the backend's app package directly (Agent Core, LLM
# provider, tool registry, DB session) since for V1 both processes run
# on the same machine against the same SQLite database (see
# app/database/db.py for how that shared path is resolved). Only the
# message/action queue itself (pending/complete/fail, confirmed
# actions, heartbeat) goes over HTTP, which is what would let this
# worker talk to a remote backend later without changes to the Agent
# Core.
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT))

from app.agent.core import CypherAgent, PipelineResult  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database.db import init_db, session_scope  # noqa: E402
from app.database.models import Message  # noqa: E402
from app.llm.base import LLMUnavailableError  # noqa: E402
from app.llm.factory import get_cloud_llm_provider, get_llm_provider  # noqa: E402
from app.tools.defaults import build_default_registry  # noqa: E402

from agent.client import BackendClient  # noqa: E402

logger = logging.getLogger("cypher.agent.sync_worker")


class SyncWorker:
    def __init__(self, client: BackendClient):
        self.client = client
        settings = get_settings()
        self.cypher = CypherAgent(
            llm=get_llm_provider(settings),
            tools=build_default_registry(),
            cloud_llm=get_cloud_llm_provider(settings),
        )

    async def poll_once(self) -> int:
        """One full cycle: process new pending messages, then confirmed
        actions. Returns how many items were processed in total."""
        processed = 0

        try:
            pending = await self.client.get_pending_messages()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not reach backend for pending messages: %s", exc)
            pending = []

        if pending:
            logger.info("Found %d pending message(s).", len(pending))
            for msg in pending:
                await self._process_message(msg)
                processed += 1

        try:
            confirmed = await self.client.get_confirmed_actions()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not reach backend for confirmed actions: %s", exc)
            confirmed = []

        if confirmed:
            logger.info("Found %d confirmed action(s).", len(confirmed))
            for action in confirmed:
                await self._process_confirmed_action(action)
                processed += 1

        return processed

    async def _process_message(self, msg: dict) -> None:
        message_id = msg["id"]
        content = msg["content"]
        retry_count = msg.get("retry_count", 0)
        logger.info("Processing message %s (retry_count=%d)", message_id, retry_count)
        try:
            with session_scope() as db:
                result = await self.cypher.run_pipeline(db, content)
            await self._apply_result(message_id, result)
        except LLMUnavailableError:
            logger.error("Local LLM is unavailable while processing message %s", message_id)
            await self._retry_or_fail(message_id, retry_count, "Local LLM is unavailable.")
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error processing message %s", message_id)
            await self._safe_fail_message(message_id, "Die Aktion konnte nicht ausgeführt werden.")

    async def _retry_or_fail(self, message_id: int, retry_count: int, error: str) -> None:
        """A transient failure (local LLM unreachable) requeues the
        message with backoff instead of failing it permanently, up to
        MESSAGE_MAX_RETRIES - see project spec section 19."""
        settings = get_settings()
        if retry_count >= settings.MESSAGE_MAX_RETRIES:
            logger.warning("Message %s exhausted %d retries - failing permanently.", message_id, retry_count)
            await self._safe_fail_message(
                message_id, f"{error} (nach {retry_count} Versuchen aufgegeben.)"
            )
            return

        next_retry_count = retry_count + 1
        backoff = settings.MESSAGE_RETRY_BACKOFF_SECONDS * next_retry_count
        next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        try:
            await self.client.retry_message(
                message_id, next_retry_count, next_retry_at.isoformat(), error
            )
            logger.info(
                "Message %s requeued for retry %d/%d in %.0fs.",
                message_id,
                next_retry_count,
                settings.MESSAGE_MAX_RETRIES,
                backoff,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not requeue message %s for retry: %s", message_id, exc)

    async def _process_confirmed_action(self, action: dict) -> None:
        action_id = action["id"]
        tool_name = action["tool_name"]
        arguments = action["arguments"]
        observations = action["observations"]
        message_id = action.get("message_id")
        logger.info("Executing confirmed action %s (%s)", action_id, tool_name)

        tool = self.cypher.tools.get(tool_name)
        if tool is None:
            logger.error("Confirmed action %s references unknown tool '%s'", action_id, tool_name)
            await self._safe_fail_action(action_id, f"Unknown tool '{tool_name}'.")
            if message_id is not None:
                await self._safe_fail_message(message_id, f"Unbekanntes Tool '{tool_name}'.")
            return

        try:
            with session_scope() as db:
                tool_result = await self.cypher.execute_tools(db, tool, arguments, approval_id=f"legacy-action-{action_id}")
                observations = observations + [self.cypher.build_observation(tool_name, arguments, tool_result)]
                await self.client.complete_action(action_id, result=tool_result.data if tool_result.success else {"error": tool_result.error})

                if message_id is None:
                    return

                message = db.get(Message, message_id)
                if message is None:
                    logger.error("Confirmed action %s references missing message %s", action_id, message_id)
                    return

                result = await self.cypher.run_pipeline(db, message.content, observations=observations)
            await self._apply_result(message_id, result)
        except LLMUnavailableError:
            logger.error("Local LLM unavailable while resuming message %s", message_id)
            if message_id is not None:
                await self._safe_fail_message(message_id, "Local LLM is unavailable.")
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error executing confirmed action %s", action_id)
            await self._safe_fail_action(action_id, "Die Aktion konnte nicht ausgeführt werden.")
            if message_id is not None:
                await self._safe_fail_message(message_id, "Die Aktion konnte nicht ausgeführt werden.")

    async def _apply_result(self, message_id: int, result: PipelineResult) -> None:
        if result.done:
            await self.client.complete_message(
                message_id, result.reply, processed_by=result.processed_by, observations=result.observations
            )
            logger.info("Message %s completed (processed_by=%s).", message_id, result.processed_by)
            return

        await self.client.create_pending_action(
            message_id=message_id,
            tool_name=result.pending_tool,
            arguments=result.pending_arguments or {},
            observations=result.observations,
            reply=result.reply,
        )
        logger.info("Message %s paused, awaiting confirmation for '%s'.", message_id, result.pending_tool)

    async def _safe_fail_message(self, message_id: int, error: str) -> None:
        try:
            await self.client.fail_message(message_id, error)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not report failure for message %s to backend: %s", message_id, exc)

    async def _safe_fail_action(self, action_id: int, error: str) -> None:
        try:
            await self.client.fail_action(action_id, error)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not report failure for action %s to backend: %s", action_id, exc)

    async def run_forever(self, poll_interval: float) -> None:
        init_db()
        logger.info("Sync worker started. Polling every %.1fs.", poll_interval)
        while True:
            await self.poll_once()
            await asyncio.sleep(poll_interval)
