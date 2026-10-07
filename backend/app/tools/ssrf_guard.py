"""
SSRF protection for fetch_url (project spec section 26). Blocks
requests to private/reserved IP ranges and cloud metadata endpoints, so
a URL the LLM decides to fetch can't be used to probe the user's own
LAN (including this very Cypher backend) or a cloud metadata service
if this is ever run on a VM.

Approach adapted from the SSRF-guard pattern in the open-source
OpenJarvis project (Apache 2.0) - simplified to pure Python only (their
version prefers a compiled Rust backend with this as a fallback; Cypher
has no such extension, so this is just the fallback logic on its own).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "metadata.google.internal",
        "metadata.google.com",
        "100.100.100.200",  # Alibaba Cloud metadata
    }
)

_BLOCKED_CIDR = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
]


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """Embedded IPv4 for IPv4-mapped (::ffff:a.b.c.d) / IPv4-compatible
    (::a.b.c.d) IPv6 addresses - without this, ::ffff:127.0.0.1 would
    bypass the loopback check since it isn't literally in 127.0.0.0/8."""
    mapped = addr.ipv4_mapped
    if mapped is not None:
        return mapped
    packed = addr.packed
    if packed[:12] == b"\x00" * 12 and addr not in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1")):
        return ipaddress.IPv4Address(packed[12:])
    return None


def is_private_ip(ip_str: str) -> bool:
    """True for every address that is not plain public internet: private,
    loopback, link-local, multicast, reserved - and the carrier-grade NAT
    range 100.64.0.0/10, which Tailscale uses for the user's own devices."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(addr)
        if embedded is not None:
            addr = embedded
    return any(addr in net for net in _BLOCKED_CIDR) or not addr.is_global


def resolve_public(url: str) -> tuple[str | None, list[str]]:
    """Validate `url` and resolve its host ONCE.

    Returns (reason_if_blocked, public_ips). The caller must connect to one
    of the returned IPs (not re-resolve the name): otherwise an attacker's
    DNS could answer with a public IP for this check and 127.0.0.1 for the
    actual request a moment later (DNS rebinding).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme '{parsed.scheme}' - only http/https are allowed.", []
    hostname = parsed.hostname
    if not hostname:
        return "No hostname in URL.", []
    if hostname in _BLOCKED_HOSTS:
        return f"Blocked host: {hostname} (cloud metadata endpoint).", []
    try:
        ipaddress.ip_address(hostname)
        candidates = [hostname]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            return f"Could not resolve host '{hostname}'.", []
        candidates = list(dict.fromkeys(info[4][0] for info in infos))
    blocked = [ip for ip in candidates if is_private_ip(ip)]
    if blocked:
        return f"URL '{hostname}' resolves to a private/reserved IP: {blocked[0]}.", []
    return None, candidates


def check_ssrf(url: str) -> str | None:
    """Returns a reason string if `url` should be blocked, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme '{parsed.scheme}' - only http/https are allowed."

    hostname = parsed.hostname
    if not hostname:
        return "No hostname in URL."

    if hostname in _BLOCKED_HOSTS:
        return f"Blocked host: {hostname} (cloud metadata endpoint)."

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if is_private_ip(hostname):
            return f"URL resolves to a private/reserved IP: {hostname}."
        return None

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _stype, _proto, _canonname, sockaddr in resolved:
            ip = sockaddr[0]
            if is_private_ip(ip):
                return f"URL '{hostname}' resolves to a private/reserved IP: {ip}."
    except socket.gaierror:
        pass  # DNS resolution failed - let the actual request fail naturally.

    return None
