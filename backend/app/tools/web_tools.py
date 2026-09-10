"""
Minimal web research tool (project spec section 26). Fetches a single
URL and returns its readable text, so Cypher can look something up and
cite the source - it does not do open-ended web search (no search API
configured), only "read this specific page". SAFE (read-only, no local
side effects), but every fetch goes through ssrf_guard.check_ssrf()
first so the LLM can't use it to probe the user's own LAN or a cloud
metadata endpoint.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx

from app.tools.base import Tool, ToolResult, ToolSecurity
from app.tools.ssrf_guard import check_ssrf

FETCH_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 2_000_000  # 2 MB - plenty for an article, caps memory use
MAX_TEXT_CHARS = 20_000  # don't dump a huge page into the LLM's context
USER_AGENT = "JarvisBot/1.0 (+local personal assistant; fetch_url tool)"


class _TextExtractor(HTMLParser):
    """Very small HTML->text extractor - no external dependency (e.g.
    BeautifulSoup). Good enough for "read this article", not a full
    renderer: drops script/style content, collapses whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.title_chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_chunks.append(data)
        else:
            self.chunks.append(data)

    def text(self) -> str:
        raw = " ".join(chunk.strip() for chunk in self.chunks if chunk.strip())
        return re.sub(r"\s+", " ", raw).strip()

    def title(self) -> str:
        return " ".join(self.title_chunks).strip()


class WebSearchTool(Tool):
    """Placeholder web search - no real search API is wired up yet.
    Exists so the tool-calling loop has a "search the web" affordance
    to route generic lookup questions to; swap the body for a real
    provider (Brave/SerpAPI/etc.) later without touching the schema or
    any caller."""

    name = "web_search"
    description = (
        "Search the web for a query and return a short list of results (title + snippet + url). "
        "Use this for open-ended questions you don't know the answer to and don't have an exact URL for. "
        "PLACEHOLDER: not wired to a real search engine yet, results are stubbed."
    )
    parameters = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    security = ToolSecurity.SAFE

    async def execute(self, db=None, query: str = "", **_) -> ToolResult:
        return ToolResult(
            success=True,
            data={
                "query": query,
                "results": [],
                "note": "web_search is a placeholder - no real search backend is configured yet.",
            },
        )


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Fetch a specific web page and return its readable text, so you can look "
        "something up and answer using it. Always cite the URL as the source when "
        "you use information from this tool. Not a search engine - give it an exact URL."
    )
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    security = ToolSecurity.SAFE

    async def execute(self, db=None, url: str = "", **_) -> ToolResult:
        blocked_reason = check_ssrf(url)
        if blocked_reason:
            return ToolResult(success=False, error=f"Refused: {blocked_reason}")

        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            ) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        return ToolResult(
                            success=False, error=f"Unsupported content type '{content_type}' - only text/HTML pages are supported."
                        )

                    raw_bytes = bytearray()
                    async for chunk in resp.aiter_bytes():
                        raw_bytes.extend(chunk)
                        if len(raw_bytes) > MAX_RESPONSE_BYTES:
                            break
                    # Re-check the final (post-redirect) URL, since the SSRF
                    # guard above only validated the URL we were given.
                    final_reason = check_ssrf(str(resp.url))
                    if final_reason:
                        return ToolResult(success=False, error=f"Refused after redirect: {final_reason}")
        except httpx.HTTPStatusError as exc:
            return ToolResult(success=False, error=f"Request failed with status {exc.response.status_code}.")
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Could not fetch URL: {exc}")

        html = raw_bytes.decode("utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(html)
        text = extractor.text()
        truncated = len(text) > MAX_TEXT_CHARS
        return ToolResult(
            success=True,
            data={
                "url": str(resp.url),
                "title": extractor.title() or None,
                "content": text[:MAX_TEXT_CHARS],
                "truncated": truncated,
            },
        )
