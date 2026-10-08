"""
Start the Cypher backend with API_HOST/API_PORT from .env.

Usage (from backend/, venv active):
    python run.py            # bind to API_HOST:API_PORT from .env
    python run.py --reload   # same, with autoreload for development

Prefer this over a hand-typed `uvicorn ... --host ...`: only run.py sees
the real bind address, which the token safety check depends on (see
docs/MOBILE.md), it prints configuration errors in readable German, and
it lets stop_cypher.bat shut the server down cleanly
(POST /api/system/shutdown).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
    if reload:
        # log_config=None: keep our logging setup (app/core/log.py) instead of uvicorn's.
        uvicorn.run("app.main:app", host=settings.API_HOST, port=settings.API_PORT, reload=True, log_config=None, access_log=False)
        return 0

    from app.main import app  # noqa: PLC0415

    server = uvicorn.Server(uvicorn.Config(app, host=settings.API_HOST, port=settings.API_PORT, log_config=None, access_log=False))
    app.state.uvicorn_server = server
    pid_file = settings.data_dir_path / "cypher.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    try:
        server.run()
    finally:
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
