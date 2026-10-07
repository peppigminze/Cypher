"""
Entrypoint that actually honors API_HOST/API_PORT from .env (see
app/config.py) - Phase 8, for binding the backend to a specific
interface (e.g. your Tailscale IP) instead of copy-pasting
`--host`/`--port` flags. See TAILSCALE.md for the full setup.

Usage (from backend/):
    python run.py            # bind to API_HOST:API_PORT from .env
    python run.py --reload   # same, with autoreload for local dev

The plain `uvicorn app.main:app --host ... --port ...` invocation still
documented in README.md for local dev works exactly as before - this
is an additional, explicit way to start the server that can't drift
from what .env actually says, which matters once the bind address is
security-relevant (Tailscale reachability) rather than just a dev
convenience.
"""
from __future__ import annotations

import sys

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    reload = "--reload" in sys.argv
    print(f"Starting Cypher backend on {settings.API_HOST}:{settings.API_PORT} (reload={reload})")
    uvicorn.run("app.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=reload)


if __name__ == "__main__":
    main()
