"""web_search / fetch_url without real network: DNS resolution and HTTP
are both faked, so the SSRF logic can be tested exactly."""
from __future__ import annotations

import httpx
import pytest

from app.tools import ssrf_guard, web_tools
from app.tools.web_tools import FetchUrlTool, WebSearchTool, _DuckDuckGoParser

PUBLIC_IP = "93.184.216.34"


@pytest.fixture()
def fake_net(monkeypatch):
    """dns: hostname -> ip ; routes: (host header, path) -> response factory."""
    dns: dict[str, str] = {"example.com": PUBLIC_IP, "other.example": "93.184.216.35", "evil.example": "127.0.0.1"}
    routes: dict = {}
    seen: list[httpx.Request] = []

    def fake_resolve(url: str):
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        if host is None:
            return "No hostname in URL.", []
        try:
            import ipaddress

            ipaddress.ip_address(host)
            ip = host
        except ValueError:
            ip = dns.get(host)
            if ip is None:
                return f"Could not resolve host '{host}'.", []
        if ssrf_guard.is_private_ip(ip):
            return f"URL '{host}' resolves to a private/reserved IP: {ip}.", []
        return None, [ip]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.headers.get("host"), request.url.path)
        factory = routes.get(key)
        if factory is None:
            return httpx.Response(404)
        return factory(request)

    monkeypatch.setattr(web_tools, "resolve_public", fake_resolve)
    monkeypatch.setattr(web_tools, "_transport", httpx.MockTransport(handler))
    return dns, routes, seen


def html_page(body: str, title: str = "Test Page") -> callable:
    return lambda req: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=f"<html><head><title>{title}</title></head><body>{body}</body></html>".encode())


async def test_fetch_extracts_text_and_marks_output_untrusted(fake_net):
    _, routes, seen = fake_net
    routes[("example.com", "/article")] = html_page("<nav>Menü</nav><script>evil()</script><p>Hallo Welt.</p><p>Zweiter Absatz.</p>")
    tool = FetchUrlTool()
    result = await tool.execute(url="https://example.com/article")
    assert result.success is True
    assert result.data["title"] == "Test Page"
    assert "Hallo Welt." in result.data["content"] and "Zweiter Absatz." in result.data["content"]
    assert "evil()" not in result.data["content"] and "Menü" not in result.data["content"]
    assert tool.untrusted_output is True
    # Connected to the pinned IP, not to the name.
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions.get("sni_hostname") == "example.com"


async def test_fetch_truncates_to_configured_limit(fake_net, monkeypatch):
    _, routes, _ = fake_net
    monkeypatch.setenv("FETCH_MAX_CHARS", "1000")
    from app.config import get_settings

    get_settings.cache_clear()
    routes[("example.com", "/long")] = html_page("<p>" + "wort " * 2000 + "</p>")
    try:
        result = await FetchUrlTool().execute(url="https://example.com/long")
    finally:
        get_settings.cache_clear()
    assert result.data["truncated"] is True
    assert len(result.data["content"]) == 1000


async def test_fetch_rejects_non_text_content(fake_net):
    _, routes, _ = fake_net
    routes[("example.com", "/file.pdf")] = lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
    result = await FetchUrlTool().execute(url="https://example.com/file.pdf")
    assert result.success is False and "content type" in result.error


async def test_private_target_is_refused_before_any_request(fake_net):
    _, _, seen = fake_net
    for url in ("http://127.0.0.1/admin", "http://evil.example/", "http://100.101.102.103/", "http://192.168.1.1/"):
        result = await FetchUrlTool().execute(url=url)
        assert result.success is False and "Refused" in result.error
    assert seen == []


async def test_redirect_to_private_address_is_never_requested(fake_net):
    _, routes, seen = fake_net
    routes[("example.com", "/r")] = lambda req: httpx.Response(302, headers={"location": "http://evil.example/steal"})
    result = await FetchUrlTool().execute(url="https://example.com/r")
    assert result.success is False and "Refused after redirect" in result.error
    assert [r.headers["host"] for r in seen] == ["example.com"]


async def test_safe_redirect_is_followed(fake_net):
    _, routes, _ = fake_net
    routes[("example.com", "/old")] = lambda req: httpx.Response(301, headers={"location": "https://other.example/new"})
    routes[("other.example", "/new")] = html_page("<p>Neu hier.</p>", title="Neu")
    result = await FetchUrlTool().execute(url="https://example.com/old")
    assert result.success is True and result.data["url"] == "https://other.example/new"


async def test_non_http_scheme_refused():
    result = await FetchUrlTool().execute(url="file:///C:/Windows/win.ini")
    assert result.success is False and "Refused" in result.error


DDG_HTML = """
<div class="result results_links"><div class="links_main">
  <a rel="nofollow" class="result__a" href="https://filialen.migros.ch/de/bern">Migros Filialen</a>
  <a class="result__snippet" href="https://filialen.migros.ch/de/bern">Hier sind die <b>Öffnungszeiten</b>.</a>
</div></div>
<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Anzeige</a></div>
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sbb.ch%2Fmigros&amp;rut=x">Migros - Bahnhof Bern | SBB</a>
  <a class="result__snippet" href="#">Shop im Bahnhof.</a></div>
"""


def test_duckduckgo_parser_extracts_results_and_skips_ads():
    parser = _DuckDuckGoParser()
    parser.feed(DDG_HTML)
    parser.close()
    assert [r["title"] for r in parser.results] == ["Migros Filialen", "Migros - Bahnhof Bern | SBB"]
    assert parser.results[0]["snippet"] == "Hier sind die Öffnungszeiten."
    assert parser.results[1]["url"] == "https://www.sbb.ch/migros"


async def test_web_search_uses_post_and_returns_results(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=DDG_HTML)

    monkeypatch.setattr(web_tools, "_transport", httpx.MockTransport(handler))
    tool = WebSearchTool()
    result = await tool.execute(query="Migros Bern Öffnungszeiten")
    assert result.success is True
    assert result.data["count"] == 2
    assert requests[0].method == "POST"
    assert tool.untrusted_output is True


async def test_web_search_reports_bot_block_in_german(monkeypatch):
    monkeypatch.setattr(web_tools, "_transport", httpx.MockTransport(lambda r: httpx.Response(202, text="<html>anomaly detected</html>")))
    result = await WebSearchTool().execute(query="test")
    assert result.success is False and "Bot-Schutz" in result.error


async def test_searxng_provider(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_URL", "http://searx.test")
    get_settings.cache_clear()
    monkeypatch.setattr(web_tools, "_transport", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"results": [{"title": "T", "content": "S", "url": "https://x.example"}]})
    ))
    try:
        result = await WebSearchTool().execute(query="abc")
    finally:
        get_settings.cache_clear()
    assert result.data["provider"] == "searxng" and result.data["results"][0]["title"] == "T"
