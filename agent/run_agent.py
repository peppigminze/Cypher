"""
Cypher local PC agent entrypoint.

Run from the project root (see README.md):

    python -m agent.run_agent

This starts two concurrent loops:
    1. Heartbeat  - tells the backend "this PC is online" every
                    AGENT_HEARTBEAT_INTERVAL_SECONDS.
    2. Sync worker - polls for pending messages and processes them
                     using the local Cypher Agent Core + local LLM.

If the local LLM or the backend is unreachable, this process logs the
problem and keeps retrying - it never crashes outright (see project
spec section 24, "error handling").
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402

from agent.client import BackendClient  # noqa: E402
from agent.sync_worker import SyncWorker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cypher.agent")


async def heartbeat_loop(client: BackendClient, interval: float) -> None:
    while True:
        try:
            await client.send_heartbeat()
            logger.debug("Heartbeat sent.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not send heartbeat (backend unreachable?): %s", exc)
        await asyncio.sleep(interval)


async def main() -> None:
    settings = get_settings()
    logger.info("Cypher agent starting.")
    logger.info("Backend: %s", settings.BACKEND_BASE_URL)
    logger.info("LLM provider: %s (%s)", settings.LLM_PROVIDER, settings.LOCAL_LLM_MODEL)

    client = BackendClient(base_url=settings.BACKEND_BASE_URL, agent_token=settings.AGENT_TOKEN)
    worker = SyncWorker(client)

    await asyncio.gather(
        heartbeat_loop(client, settings.AGENT_HEARTBEAT_INTERVAL_SECONDS),
        worker.run_forever(settings.AGENT_POLL_INTERVAL_SECONDS),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Cypher agent stopped.")
