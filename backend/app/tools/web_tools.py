"""
Web tools: real web search and fetching a page's readable text.

web_search
    Local-first, no API key: DuckDuckGo's HTML endpoint (via POST - their
    GET endpoint answers scripted clients with a bot check) or, if
    configured, a self-hosted SearXNG instance (SEARXNG_URL, JSON API).
    Chosen over paid search APIs because Cypher must work without accounts.

fetch_url
    Hardened against SSRF (the LLM could otherwise use it to probe the
    user's LAN, the Tailscale network or this very backend):
      * every hop is validated - redirects are followed manually, never by
        the HTTP client, so a redirect to 127.0.0.1 is never requested;
      * the host is resolved once and the connection goes to that exact IP
        (with the original Host header / TLS SNI), so DNS rebinding cannot
        swap in a private address between check and request;
      * size, content type and extracted text length are capped.

Both return untrusted content (see app/security/untrusted.py).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx
from pydantic import Field

from app.config import Settings, get_settings
from app.tools.base import ArgsModel, RiskTier, Tool, ToolResult, register_tool
from app.tools.ssrf_guard import check_ssrf, resolve_public

logger = logging.getLogger("cypher.tools.web")

FETCH_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
# Kept for compatibility; the effective limit is FETCH_MAX_CHARS from config.
MAX_TEXT_CHARS = 20_000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CypherAssistant/1.0"
_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")

# Tests replace this with httpx.MockTransport; None = real network.
_transport: Optional[httpx.AsyncBaseTransport] = None


def _client(timeout: float = FETCH_TIMEOUT_SECONDS) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}, transport=_transport)


# ------------------------------------------------------------------ HTML -> text
class _TextExtractor(HTMLParser):
    """Small HTML -> text converter (no BeautifulSoup dependency). Drops
    scripts, styles and page chrome (nav/header/footer/aside/forms),
    keeps paragraph breaks so sentences don't run together."""

    _SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "iframe"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.title_chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_chunks.append(data)
        else:
            self.chunks.append(data)

    def text(self) -> str:
        raw = "".join(self.chunks)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)

    def title(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.title_chunks)).strip()


# ------------------------------------------------------------------ fetch_url
def _pinned_request_url(url: str, ip: str) -> str:
    parsed = urlparse(url)
    host = f"[{ip}]" if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address) else ip
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


async def fetch_text(url: str, max_chars: int) -> Dict[str, Any]:
    """Fetch `url` safely and return {"url", "title", "content", "truncated"}.
    Raises ValueError with a user-presentable reason on refusal/failure."""
    current = url
    # Validate the first target before any client exists (DNS lookup is
    # blocking -> worker thread).
    reason, ips = await asyncio.to_thread(resolve_public, current)
    if reason:
        raise ValueError(f"Refused: {reason}")
    async with _client() as client:
        for hop in range(MAX_REDIRECTS + 1):
            if hop > 0:
                reason, ips = await asyncio.to_thread(resolve_public, current)
                if reason:
                    raise ValueError(f"Refused after redirect: {reason}")
            parsed = urlparse(current)
            headers = {"Host": parsed.netloc}
            extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
            request = client.build_request("GET", _pinned_request_url(current, ips[0]), headers=headers, extensions=extensions)
            response = await client.send(request, stream=True)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect without a target.")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"Request failed with status {response.status_code}.")
                content_type = response.headers.get("content-type", "")
                if not any(t in content_type for t in _TEXT_TYPES):
                    raise ValueError(f"Unsupported content type '{content_type}' - only text/HTML pages are supported.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        break
            finally:
                await response.aclose()
            charset = response.charset_encoding or "utf-8"
            html = body.decode(charset, errors="replace")
            if "text/plain" in content_type:
                title, text = "", html.strip()
            else:
                extractor = _TextExtractor()
                extractor.feed(html)
                title, text = extractor.title(), extractor.text()
            return {"url": current, "title": title or None, "content": text[:max_chars], "truncated": len(text) > max_chars}
    raise ValueError(f"Too many redirects (more than {MAX_REDIRECTS}).")


class FetchUrlArgs(ArgsModel):
    url: str = Field(..., description="Vollständige URL, z. B. https://example.com/artikel")


@register_tool
class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Eine bestimmte Webseite abrufen und ihren lesbaren Text zurückgeben. Nur mit einer exakten URL nutzen "
        "(für offene Fragen web_search). Nenne die URL als Quelle, wenn du Inhalte daraus verwendest."
    )
    Args = FetchUrlArgs
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True
    timeout_seconds = 40.0
    spoken_progress = "Ich lese die Seite."

    async def execute(self, db=None, url: str = "", **_) -> ToolResult:
        # Fast, network-free refusal for obviously bad targets.
        quick = check_ssrf(url) if not urlparse(url).hostname or urlparse(url).scheme not in ("http", "https") else None
        if quick:
            return ToolResult(success=False, error=f"Refused: {quick}")
        try:
            data = await fetch_text(url, get_settings().FETCH_MAX_CHARS)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Could not fetch URL: {exc.__class__.__name__}")
        return ToolResult(success=True, data=data)

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Webseite abrufen: {args.get('url', '')}"


# ------------------------------------------------------------------ web_search
class _DuckDuckGoParser(HTMLParser):
    """Parses html.duckduckgo.com result markup (a.result__a / a.result__snippet)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: List[Dict[str, str]] = []
        self._field: Optional[str] = None
        self._buf: List[str] = []
        self._current: Dict[str, str] = {}
        self._in_ad = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if tag == "div" and "result--ad" in classes:
            self._in_ad += 1
        if tag != "a":
            return
        if "result__a" in classes:
            self._flush()
            self._current = {"url": _unwrap_ddg_link(attr.get("href") or "")}
            self._field, self._buf = "title", []
        elif "result__snippet" in classes and self._current:
            self._field, self._buf = "snippet", []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._field:
            self._current[self._field] = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._current.get("url") and self._current.get("title") and "duckduckgo.com/y.js" not in self._current["url"]:
            self.results.append({"title": self._current["title"], "snippet": self._current.get("snippet", ""), "url": self._current["url"]})
        self._current = {}

    def close(self) -> None:
        super().close()
        self._flush()


def _unwrap_ddg_link(href: str) -> str:
    """DDG sometimes wraps targets as //duckduckgo.com/l/?uddg=<encoded>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target or href
    return href


async def search_duckduckgo(query: str, limit: int) -> List[Dict[str, str]]:
    async with _client(timeout=10.0) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": "ch-de"})
    blocked_page = "anomaly" in resp.text[:5000].lower() and "result__a" not in resp.text
    if resp.status_code != 200 or blocked_page:
        raise ValueError("DuckDuckGo hat die Anfrage abgelehnt (Bot-Schutz). Später erneut versuchen oder SearXNG einrichten.")
    parser = _DuckDuckGoParser()
    parser.feed(resp.text)
    parser.close()
    return parser.results[:limit]


async def search_searxng(base_url: str, query: str, limit: int) -> List[Dict[str, str]]:
    async with _client(timeout=10.0) as client:
        resp = await client.get(f"{base_url}/search", params={"q": query, "format": "json", "language": "de"})
    if resp.status_code != 200:
        raise ValueError(f"SearXNG antwortete mit HTTP {resp.status_code} (ist das JSON-Format in der Instanz aktiviert?).")
    items = resp.json().get("results", [])
    return [{"title": i.get("title", ""), "snippet": i.get("content", ""), "url": i.get("url", "")} for i in items[:limit]]


class WebSearchArgs(ArgsModel):
    query: str = Field(..., min_length=2, description="Suchbegriffe")


@register_tool
class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Im Internet suchen. Liefert Titel, kurzen Ausschnitt und URL der Treffer. Für aktuelle oder unbekannte "
        "Informationen (Öffnungszeiten, News, Fakten), nicht für Allgemeinwissen, das du sicher weisst."
    )
    Args = WebSearchArgs
    risk = RiskTier.READ_ONLY
    untrusted_output = True
    exposed_by_default = True
    timeout_seconds = 20.0
    spoken_progress = "Ich suche kurz."

    @classmethod
    def is_available(cls, settings: Settings) -> bool:
        return settings.WEB_SEARCH_PROVIDER != "off"

    async def execute(self, db=None, query: str = "", **_) -> ToolResult:
        settings = get_settings()
        limit = settings.WEB_SEARCH_MAX_RESULTS
        try:
            if settings.WEB_SEARCH_PROVIDER == "off":
                return ToolResult(success=False, error="Web search is disabled (WEB_SEARCH_PROVIDER=off).")
            if settings.WEB_SEARCH_PROVIDER == "searxng":
                if not settings.SEARXNG_URL:
                    return ToolResult(success=False, error="WEB_SEARCH_PROVIDER=searxng, but SEARXNG_URL is not set.")
                results = await search_searxng(settings.SEARXNG_URL, query, limit)
                provider = "searxng"
            else:
                results = await search_duckduckgo(query, limit)
                provider = "duckduckgo"
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Web search failed: {exc.__class__.__name__}")
        return ToolResult(success=True, data={"query": query, "provider": provider, "count": len(results), "results": results})

    def describe(self, args: Dict[str, Any]) -> str:
        return f"Im Web suchen: {args.get('query', '')}"
