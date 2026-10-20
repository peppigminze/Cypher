"""
Access tokens.

Before the overhaul the user token came from `.env` (or stayed the public
placeholder) and was baked into the frontend bundle via VITE_USER_TOKEN,
so anyone who could load the PWA had it (docs/overhaul/AUDIT.md K5).

Now:
  * If `.env` sets a real USER_TOKEN/AGENT_TOKEN, that value is used
    (existing setups keep working).
  * Otherwise a random token is generated on first start and stored in
    DATA_DIR/secrets.json (gitignored, readable only by this user).
  * The local UI never needs the token typed in: it fetches it via
    /api/auth/session, which only answers same-site requests from this
    PC (see app/security/origin.py). Remote devices (phone via Tailscale)
    pair once by entering the token or opening the pairing link.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings

logger = logging.getLogger("cypher.security.tokens")

PLACEHOLDER_TOKENS = {"change-me-user-token", "change-me-agent-token", ""}
SECRETS_FILE_NAME = "secrets.json"
_MIN_TOKEN_LENGTH = 24

_lock = threading.Lock()
_cache: dict[str, "TokenSet"] = {}


@dataclass(frozen=True)
class TokenSet:
    user_token: str
    agent_token: str
    user_source: str  # "env" | "generated"
    agent_source: str


def _secrets_path(settings: Settings) -> Path:
    return settings.data_dir_path / SECRETS_FILE_NAME


def _is_usable(token: str) -> bool:
    """Placeholders are never accepted as credentials - a fresh token is
    generated instead, so the public example value can't open the API."""
    return token.strip() not in PLACEHOLDER_TOKENS


def is_weak(token: str) -> bool:
    return len(token) < _MIN_TOKEN_LENGTH


def _load_or_create(path: Path) -> dict[str, str]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s (%s) - generating new tokens", path, exc)
    return {}


def _write(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        # Best effort: on Windows this only toggles read-only, but on other
        # systems it limits the file to the owner.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get_tokens(settings: Settings | None = None) -> TokenSet:
    """Resolve the active tokens (cached per data dir + env values)."""
    settings = settings or get_settings()
    key = f"{settings.data_dir_path}|{settings.USER_TOKEN}|{settings.AGENT_TOKEN}"
    with _lock:
        cached = _cache.get(key)
        if cached:
            return cached

        stored: dict[str, str] = {}
        need_file = not _is_usable(settings.USER_TOKEN) or not _is_usable(settings.AGENT_TOKEN)
        if need_file:
            path = _secrets_path(settings)
            stored = _load_or_create(path)
            changed = False
            for name in ("user_token", "agent_token"):
                if not _is_usable(stored.get(name, "")):
                    stored[name] = secrets.token_urlsafe(32)
                    changed = True
            if changed:
                _write(path, stored)
                logger.info("Generated new access token(s) in %s", path)

        tokens = TokenSet(
            user_token=settings.USER_TOKEN if _is_usable(settings.USER_TOKEN) else stored["user_token"],
            agent_token=settings.AGENT_TOKEN if _is_usable(settings.AGENT_TOKEN) else stored["agent_token"],
            user_source="env" if _is_usable(settings.USER_TOKEN) else "generated",
            agent_source="env" if _is_usable(settings.AGENT_TOKEN) else "generated",
        )
        _cache[key] = tokens
        return tokens


def tokens_match(a: str, b: str) -> bool:
    """Constant-time comparison (a plain `==` leaks the matching prefix length via timing)."""
    return secrets.compare_digest(a.encode(), b.encode())


def reset_cache() -> None:
    with _lock:
        _cache.clear()
