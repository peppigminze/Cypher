"""
Who is calling? Checks used for the token-free local UI bootstrap and
for WebSocket connections (browsers apply no CORS to WebSockets, so the
server must check the Origin itself).

Threats this defends against:
  * a random website in the user's browser calling http://127.0.0.1:8000
    (cross-site request) -> rejected by Origin/Sec-Fetch-Site checks;
  * DNS rebinding (evil.example resolves to 127.0.0.1, so the browser
    treats it as same-origin) -> rejected because the Host header is not
    a local host name;
  * other devices on the network -> rejected because the peer address is
    not loopback.
"""
from __future__ import annotations

import ipaddress
from typing import Mapping, Optional
from urllib.parse import urlparse

from app.config import Settings

_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


def is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    if host == "testclient":  # Starlette TestClient peer name
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _hostname(value: str) -> str:
    # "Host" header: "127.0.0.1:8000" / "[::1]:8000" / "localhost"
    if value.startswith("["):
        return value[: value.find("]") + 1]
    return value.split(":", 1)[0]


def host_header_is_local(headers: Mapping[str, str]) -> bool:
    host = headers.get("host", "")
    return _hostname(host.lower()) in _LOCAL_HOSTNAMES or host == "testserver"


def origin_allowed(origin: Optional[str], settings: Settings, *, own_host: Optional[str] = None) -> bool:
    """An Origin is acceptable if it is configured in CORS_ORIGINS or is
    the backend itself (frontend served by the backend, M7)."""
    if not origin:
        return False
    if origin in settings.cors_origins_list:
        return True
    if own_host:
        parsed = urlparse(origin)
        return parsed.netloc == own_host
    return False


def is_trusted_local_browser_request(peer_host: Optional[str], headers: Mapping[str, str], settings: Settings) -> bool:
    """True only for a request from the local UI running on this PC.

    Requires: loopback peer, a local Host header (anti DNS-rebinding), and -
    if the browser says where the request comes from - a same-origin/
    same-site fetch from an allowed origin. Browsers set Sec-Fetch-Site
    themselves; page scripts cannot forge it.
    """
    if not is_loopback(peer_host) or not host_header_is_local(headers):
        return False
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "same-site", "none"):
        return False
    origin = headers.get("origin")
    if origin is not None:
        own = headers.get("host")
        if not origin_allowed(origin, settings, own_host=own):
            return False
        if _hostname(urlparse(origin).netloc.lower()) not in _LOCAL_HOSTNAMES:
            return False
    return True


def websocket_origin_ok(headers: Mapping[str, str], settings: Settings) -> bool:
    """Browsers always send Origin on WebSocket handshakes. Non-browser
    clients (scripts, tests) send none; they still have to authenticate
    with the token, so a missing Origin is accepted."""
    origin = headers.get("origin")
    if origin is None:
        return True
    return origin_allowed(origin, settings, own_host=headers.get("host"))
