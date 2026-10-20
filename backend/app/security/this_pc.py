"""
Does a command or URL talk to THIS PC over the network? (red line)

Cypher's own API listens here. A command like
    curl.exe -X PUT http://127.0.0.1:8000/api/security/critical-confirm
could read the token (/api/auth/session) or flip the safety switch, so
any reference to this PC as a network target is `cypher_secrets` - on
the red line, it always asks, even with the safety prompt switched off.
This holds for every program (curl, wget, python, node, Invoke-WebRequest,
Start-Process ...): the whole text is scanned, not just known cmdlets.

    reference(text) -> str | None   what was found ("127.0.0.0/8", ...)

What counts as this PC:
    loopback      127.0.0.0/8 in every inet_aton spelling (127.1, 0x7f.1,
                  0177.0.0.1, 2130706433 as a URL host), localhost,
                  localhost., *.localhost, ::1, ::ffff:127.x, 0.0.0.0, [::]
    backend       API_HOST:API_PORT if API_HOST is not loopback; with a
                  wildcard API_HOST every local interface address and the
                  computer name with API_PORT
    tailscale     this machine's tailnet name(s) and IPs, any port
                  (`tailscale serve` publishes Cypher there)

Before scanning, what the deterministic rules can undo is undone:
PowerShell backtick escapes, 'a' + 'b' concatenation, (..) -join '.',
%xx URL escapes, full-width digits (NFKC) and base64 payloads
(-EncodedCommand, FromBase64String). Anything built at run time beyond
that is not visible here - such commands stay `critical` through the
classifier's other rules.

Only tool calls are classified. Cypher's own internal requests (Ollama,
health checks, the Start menu index) never go through here.

The Tailscale identity is read with `tailscale status --json` in a
background thread (never in the classification path) and cached.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import re
import socket
import threading
import time
import unicodedata
from typing import Iterable, List, Optional, Tuple
from urllib.parse import unquote

logger = logging.getLogger("cypher.security.this_pc")

TAILSCALE_TTL_SECONDS = 300.0

_URL_HOST = re.compile(r"[a-z][a-z0-9+.\-]*:/{2}(?:[^/\s@'\"]*@)?(\[[^\]\s/]*\]|[^/\s:'\"?#\\)]*)(?::(\d{1,5}))?")
_UNC_HOST = re.compile(r"(?:^|[\s'\"(=])\\\\([^\\\s'\"]+)")
_HOST_PORT = re.compile(r"(?<![\w.\-@:/])(\[[0-9a-f:.]+\]|[a-z0-9_][a-z0-9_.\-]*):(\d{1,5})(?![\d])")
_DOTTED = re.compile(r"(?<![\w.])((?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){1,3})\.?(?!\w)")
_IPV6 = re.compile(r"(\[)?([0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}(?::\d{1,3}(?:\.\d{1,3}){3})?)(\])?")
_LOCALHOST = re.compile(r"(?<![\w-])(?:[\w-]+\.)*localhost\.?(?![\w-])")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_CONCAT = re.compile(r"(['\"])\s*\+\s*(['\"])")
_JOIN = re.compile(r"\(\s*((?:['\"]?[\w.]+['\"]?\s*,\s*)+['\"]?[\w.]+['\"]?)\s*\)\s*-join\s*(['\"])(.*?)\2")


# ------------------------------------------------------------------ identities
_lock = threading.Lock()
_tailscale = {"at": float("-inf"), "hosts": frozenset(), "running": False, "pinned": False}


def set_tailscale_hosts(hosts: Iterable[str], *, pinned: bool = False) -> None:
    """Tests (pinned=True: never look up) and the background refresh."""
    with _lock:
        _tailscale.update(at=time.monotonic(), hosts=frozenset(h.lower().rstrip(".") for h in hosts if h), running=False, pinned=pinned)


def _refresh_tailscale() -> None:
    hosts: Optional[frozenset] = None
    try:
        from app.ops import tailscale  # noqa: PLC0415

        raw = tailscale.status_json()
        hosts = tailscale.self_identities(raw) if raw else frozenset()
    except Exception:  # noqa: BLE001 - a failed lookup keeps the old names
        logger.exception("Could not read this PC's Tailscale identity")
    with _lock:
        if hosts is not None:
            _tailscale["hosts"] = hosts
        _tailscale.update(at=time.monotonic(), running=False)


def warm_up() -> None:
    """Startup: read the Tailscale identity in the background."""
    with _lock:
        if _tailscale["pinned"] or _tailscale["running"]:
            return
        _tailscale["running"] = True
    threading.Thread(target=_refresh_tailscale, daemon=True, name="this-pc-tailscale").start()


def _tailscale_hosts() -> frozenset:
    with _lock:
        hosts = _tailscale["hosts"]
        stale = time.monotonic() - _tailscale["at"] > TAILSCALE_TTL_SECONDS
        refresh = stale and not _tailscale["pinned"] and not _tailscale["running"]
    if refresh:
        warm_up()
    return hosts


_backend_cache: dict = {}


def _backend_targets() -> Tuple[frozenset, int]:
    """Hosts that reach the backend on API_PORT (beyond loopback)."""
    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    key = (settings.API_HOST, settings.API_PORT)
    cached = _backend_cache.get(key)
    if cached and time.monotonic() - cached[0] < 60.0:
        return cached[1]
    result = _compute_backend_targets(settings)
    _backend_cache.clear()
    _backend_cache[key] = (time.monotonic(), result)
    return result


def _compute_backend_targets(settings) -> Tuple[frozenset, int]:
    host = (settings.API_HOST or "").strip().strip("[]").lower()
    port = settings.API_PORT
    if host in ("", "0.0.0.0", "::"):
        names = {socket.gethostname().lower()}
        try:
            import psutil  # noqa: PLC0415

            for addrs in psutil.net_if_addrs().values():
                for addr in addrs:
                    if addr.family in (socket.AF_INET, socket.AF_INET6):
                        names.add(addr.address.split("%")[0].lower())
        except Exception:  # noqa: BLE001 - the computer name is still known
            pass
        return frozenset(names), port
    return frozenset({_normal_ip(host) or host}), port


# ------------------------------------------------------------------ hosts
def _ipv4_value(text: str) -> Optional[int]:
    """inet_aton: '127.1', '0x7f.1', '0177.0.0.1', '2130706433' -> int."""
    parts = text.split(".")
    if not 1 <= len(parts) <= 4 or "" in parts:
        return None
    nums = []
    for part in parts:
        try:
            if part.startswith("0x"):
                nums.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part.startswith("0"):
                nums.append(int(part, 8))
            else:
                nums.append(int(part, 10))
        except ValueError:
            return None
    *head, last = nums
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = 0
    for n in head:
        value = value * 256 + n
    return value * 256 ** (4 - len(head)) + last


def _normal_ip(text: str) -> Optional[str]:
    value = _ipv4_value(text)
    if value is not None:
        return str(ipaddress.IPv4Address(value))
    try:
        return str(ipaddress.ip_address(text.split("%")[0]))
    except ValueError:
        return None


def _host_hit(raw: str, port: Optional[int], *, host_position: bool) -> Optional[str]:
    """`raw` is a host from a URL/UNC path/host:port (host_position) or a
    bare token. Bare single numbers ("0", "5") are never hosts."""
    host = raw.strip().strip("[]").rstrip(".").lower()
    if not host:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return "localhost"
    if ":" in host:
        try:
            ip6 = ipaddress.IPv6Address(host.split("%")[0])
        except ValueError:
            ip6 = None
        if ip6 is not None:
            mapped = ip6.ipv4_mapped
            if ip6.is_loopback or (mapped is not None and (mapped.is_loopback or mapped.is_unspecified)):
                return "::1"
            if ip6.is_unspecified and host_position:
                return "::"
    else:
        dotted = "." in host
        value = _ipv4_value(host) if (dotted or host_position) else None
        if value is not None:
            if value >> 24 == 127:
                return "127.0.0.0/8"
            if value == 0 and (host_position or host == "0.0.0.0"):
                return "0.0.0.0"
    normal = _normal_ip(host) or host
    if normal in _tailscale_hosts():
        return f"Tailscale-Adresse {normal}"
    backend, api_port = _backend_targets()
    if normal in backend and port == api_port:
        return f"Backend-Adresse {normal}:{api_port}"
    return None


# ------------------------------------------------------------------ text
def _decoded_payloads(text: str) -> List[str]:
    """Readable base64 payloads (UTF-16LE as -EncodedCommand uses, or UTF-8)."""
    out = []
    for token in _BASE64.findall(text):
        padded = token + "=" * (-len(token) % 4)
        try:
            data = base64.b64decode(padded, validate=True)
        except ValueError:
            continue
        for encoding in ("utf-16-le", "utf-8"):
            try:
                decoded = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            printable = sum(ch.isprintable() or ch.isspace() for ch in decoded)
            if decoded and printable / len(decoded) > 0.95:
                out.append(decoded)
                break
    return out


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("`", "")  # PowerShell escape character
    previous = None
    while previous != text:  # 'http://127.0.0.' + '1' -> 'http://127.0.0.1'
        previous = text
        text = _CONCAT.sub("", text)
    text = _JOIN.sub(lambda m: m.group(3).join(_strip_quotes(p) for p in m.group(1).split(",")), text)
    return unquote(text)


def _strip_quotes(part: str) -> str:
    return part.strip().strip("'\"")


def _scan(text: str) -> Optional[str]:
    for match in _URL_HOST.finditer(text):
        hit = _host_hit(match.group(1), int(match.group(2)) if match.group(2) else None, host_position=True)
        if hit:
            return hit
    for match in _UNC_HOST.finditer(text):
        hit = _host_hit(match.group(1), None, host_position=True)
        if hit:
            return hit
    for match in _HOST_PORT.finditer(text):
        # "00:30" is a time, not 0.0.0.0 - a bare number is no host here
        hit = _host_hit(match.group(1), int(match.group(2)), host_position=not match.group(1).isdigit())
        if hit:
            return hit
    if _LOCALHOST.search(text):
        return "localhost"
    for match in _DOTTED.finditer(text):
        hit = _host_hit(match.group(1), None, host_position=False)
        if hit:
            return hit
    for match in _IPV6.finditer(text):
        if match.group(2).count(":") >= 2:
            hit = _host_hit(match.group(2), None, host_position=bool(match.group(1) and match.group(3)))
            if hit:
                return hit
    names = _tailscale_hosts()
    for name in names:
        if not _normal_ip(name) and re.search(rf"(?<![\w.-]){re.escape(name)}\.?(?![\w-])", text):
            return f"Tailscale-Name {name}"
    return None


def reference(text: str, _depth: int = 0) -> Optional[str]:
    """What in `text` addresses this PC over the network, or None."""
    if not text or _depth > 2:
        return None
    hit = _scan(_normalize(text))
    if hit:
        return hit
    for payload in _decoded_payloads(unicodedata.normalize("NFKC", text)):
        hit = reference(payload, _depth + 1)
        if hit:
            return hit
    return None
