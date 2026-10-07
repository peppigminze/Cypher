"""Compatibility re-export - the sandbox lives in app/security/paths.py."""
from __future__ import annotations

from app.security.paths import PathNotAllowedError, allowed_roots, resolve_allowed_path

__all__ = ["PathNotAllowedError", "allowed_roots", "resolve_allowed_path"]
