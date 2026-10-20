"""
The built web app (frontend/dist) served by the backend itself (M7):
one address for everything - http://127.0.0.1:8000 on the PC, and
https://<pc>.<tailnet>.ts.net on the phone via `tailscale serve` (docs/MOBILE.md).

  * Single-page app: any GET that is not an API path and not a file in
    dist/ answers with index.html, so reloading /irgendwas works. Paths that
    look like files (assets/..., *.js) stay 404 when missing.
  * Caching: index.html, sw.js and the manifest are "no-cache" (an update
    is picked up on the next load); files under assets/ have a content hash
    in their name and are cached for a year.
  * Only files inside dist/ can be served (path traversal is refused).

Registered last, so every API route wins.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import Settings

logger = logging.getLogger("cypher.api.frontend")

# Never answered with the app shell: backend-only paths.
_BACKEND_PREFIXES = ("api/", "health", "speak", "transcribe", "docs", "openapi.json", "redoc")
_NO_CACHE = {"index.html", "sw.js", "manifest.webmanifest", "registerSW.js"}


def _headers(name: str) -> dict:
    if name in _NO_CACHE or name.endswith(".html"):
        return {"Cache-Control": "no-cache"}
    if name.startswith("assets/"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "public, max-age=3600"}


def build_router(settings: Settings) -> Optional[APIRouter]:
    dist = settings.frontend_dist_path
    index = dist / "index.html"
    if not settings.SERVE_FRONTEND:
        return None
    if not index.is_file():
        logger.warning("Frontend not built (%s missing) - only the API is served. Run setup.bat or: cd frontend && npm run build", index)
        return None
    root = dist.resolve()
    router = APIRouter(include_in_schema=False)

    @router.get("/{path:path}")
    async def frontend(path: str) -> FileResponse:
        if path.startswith(_BACKEND_PREFIXES):
            raise HTTPException(status_code=404, detail="Nicht gefunden.")
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and (candidate == root or root in candidate.parents):
                rel = candidate.relative_to(root).as_posix()
                return FileResponse(candidate, headers=_headers(rel))
            if path.startswith("assets/") or PurePosixPath(path).suffix:
                # A missing file stays a 404: answering with index.html would make
                # the browser run HTML as a script (e.g. an old chunk after an update).
                raise HTTPException(status_code=404, detail="Nicht gefunden.")
        return FileResponse(index, headers=_headers("index.html"))

    logger.info("Serving the web app from %s", root)
    return router

