"""
Start the Cypher backend with API_HOST/API_PORT from .env.

Usage (from backend/, venv active):
    python run.py            # bind to API_HOST:API_PORT from .env
    python run.py --reload   # same, with autoreload for development

Prefer this over a hand-typed `uvicorn ... --host ...`: only run.py sees
the real bind address, which the token safety check depends on (see
TAILSCALE.md), and it prints configuration errors in readable German.
"""
from __future__ import annotations

import sys

import uvicorn

from app.config import ConfigError, get_settings


def main() -> int:
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    from app.core.log import setup_logging  # noqa: PLC0415

    setup_logging(settings)
    reload = "--reload" in sys.argv
    print(f"Starting Cypher backend on {settings.API_HOST}:{settings.API_PORT} (reload={reload})")
    # log_config=None: keep our logging setup (app/core/log.py) instead of uvicorn's.
    uvicorn.run("app.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=reload, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
