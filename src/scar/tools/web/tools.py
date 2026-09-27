"""Web research tools (C9.8): search → fetch → extract. Every claim cites URLs actually fetched in the task."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field

from scar.core.errors import CapabilityUnavailable, ToolError
from scar.core.types import RiskLevel, ToolResult, TrustLevel
from scar.providers.errors import AllProvidersFailed
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.web.extract import extract_main, hidden_text_flags, title_of

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SCAR/1.0 (+personal assistant; respects robots.txt)"
MAX_BYTES = 6 * 1024 * 1024
CACHE_TTL = 1800.0


class _FetchState:
    def __init__(self) -> None:
        self.cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.robots: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}
        self.last_hit: dict[str, float] = {}
        self.lock = asyncio.Lock()


_STATE = _FetchState()


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    cached = _STATE.robots.get(base)
    if cached is None or time.time() - cached[0] > 3600:
        rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
        try:
            r = await client.get(urljoin(base, "/robots.txt"), timeout=8.0)
            if r.status_code >= 400:
                rp = None
            else:
                assert rp is not None
                rp.parse(r.text.splitlines())
        except httpx.HTTPError:
            rp = None
        _STATE.robots[base] = (time.time(), rp)
        cached = _STATE.robots[base]
    rp = cached[1]
    return True if rp is None else rp.can_fetch("SCAR", url)


async def _pace(domain: str, min_interval: float = 1.0) -> None:
    async with _STATE.lock:
        last = _STATE.last_hit.get(domain, 0.0)
        wait = last + min_interval - time.monotonic()
        _STATE.last_hit[domain] = max(time.monotonic(), last + min_interval)
    if wait > 0:
        await asyncio.sleep(wait)


def _is_local_host(host: str) -> bool:
    """Loopback, private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local (incl. cloud metadata) or a local name."""
    import ipaddress

    host = host.strip("[]").lower()
    if host in ("localhost", "") or host.endswith((".local", ".localhost", ".internal", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


async def fetch_url(url: str, *, respect_robots: bool = True, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    cached = _STATE.cache.get(url)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return {**cached[1], "cached": True}
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "en;q=0.9"}, follow_redirects=True,
                                 timeout=httpx.Timeout(25.0, connect=10.0), transport=transport) as client:
        if respect_robots and not await _robots_allows(client, url):
            raise ToolError(f"robots.txt disallows fetching {url}", "RobotsDisallowed")
        await _pace(urlparse(url).netloc)
        try:
            async with client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                status = resp.status_code
                final_url = str(resp.url)
                encoding = resp.encoding or "utf-8"
        except httpx.HTTPError as exc:
            raise ToolError(f"could not fetch {url}: {exc}", "FetchFailed") from exc
    if status >= 400:
        raise ToolError(f"{url} returned HTTP {status}", "HttpError")
    result: dict[str, Any] = {"url": final_url, "requested_url": url, "status": status, "content_type": ctype,
                              "bytes": len(body), "truncated": size > MAX_BYTES, "flags": []}
    if ctype in ("text/html", "application/xhtml+xml") or body[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        html = body.decode(encoding, errors="replace")
        result["title"] = title_of(html)
        result["text"] = await asyncio.to_thread(extract_main, html, final_url)
        result["flags"] = hidden_text_flags(html)
    elif ctype == "application/pdf" or body[:5] == b"%PDF-":
        from scar.tools.documents.readers import read_pdf_bytes

        result["title"] = final_url.rsplit("/", 1)[-1]
        result["text"] = await asyncio.to_thread(read_pdf_bytes, body)
    elif ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
        result["title"] = final_url.rsplit("/", 1)[-1]
        result["text"] = body.decode(encoding, errors="replace")
    else:
        raise ToolError(f"unsupported content type {ctype or 'unknown'} at {url}", "UnsupportedContent")
    result["retrieved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _STATE.cache[url] = (time.time(), result)
    while len(_STATE.cache) > 100:
        _STATE.cache.popitem(last=False)
    return result


class SearchInput(ToolInput):
    query: str = Field(min_length=2)
    max_results: int = Field(8, ge=1, le=20)


class WebSearch(Tool):
    name = "web.search"
    description = "Search the web. Returns titles, URLs and snippets (then use web.fetch to read sources)."
    input_model = SearchInput
    capabilities = ("web.search",)
    categories = ("web",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 60.0

    def progress_line(self, args: SearchInput) -> str | None:
        return "Searching the web."

    async def run(self, args: SearchInput, ctx: ToolContext) -> ToolResult:
        try:
            provider, hits = await ctx.services.search.search(args.query, args.max_results)
        except AllProvidersFailed as exc:
            raise CapabilityUnavailable("web search is unavailable (no search provider reachable)",
                                        "docs/providers.md#search", str(exc)) from exc
        view = "\n".join(f"[{i + 1}] {h.title}\n    {h.url}\n    {h.snippet[:240]}" for i, h in enumerate(hits))
        return self.ok(f"{len(hits)} results for '{args.query}'",
                       {"provider": provider, "results": [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]},
                       model_view=view or "no results", source=f"search:{provider}")


class FetchInput(ToolInput):
    url: str
    max_chars: int = Field(12000, ge=500, le=200000)
    respect_robots: bool = True


class WebFetch(Tool):
    name = "web.fetch"
    description = ("Fetch a web page (or PDF/text) and extract its main content. Fetched URLs become citable "
                   "sources for this task.")
    input_model = FetchInput
    capabilities = ("web.fetch",)
    categories = ("web",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 60.0
    sensitive_args = {"url": "url"}

    def assess(self, args: FetchInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW)
        p = urlparse(args.url)
        if p.scheme not in ("http", "https"):
            a.deny(f"only http(s) URLs can be fetched (got {p.scheme or 'none'})")
        host = (p.hostname or "").lower()
        a.facts.domains.append(host)
        if _is_local_host(host):
            a.raise_to(RiskLevel.MEDIUM, "fetches from the local network")
        return a

    def progress_line(self, args: FetchInput) -> str | None:
        return f"Reading {urlparse(args.url).hostname}."

    async def run(self, args: FetchInput, ctx: ToolContext) -> ToolResult:
        page = await fetch_url(args.url, respect_robots=args.respect_robots)
        text = page.get("text") or ""
        more = f"\n…[{len(text) - args.max_chars} more characters; fetch again with larger max_chars]" if len(text) > args.max_chars else ""
        page["text"] = text[: args.max_chars]
        view = f"{page.get('title', '')}\n{page['url']} (retrieved {page['retrieved_at']})\n\n{page['text']}{more}"
        log = ctx.services.extras.setdefault("fetch_log", {}).setdefault(ctx.task_id, [])
        log.extend([args.url, page["url"]])
        del log[:-400]
        return self.ok(f"Read {page.get('title') or page['url']}", page, model_view=view, source=f"web:{page['url']}")


class ResearchInput(ToolInput):
    query: str = Field(min_length=2, description="what to research")
    max_sources: int = Field(3, ge=1, le=8)
    chars_per_source: int = Field(2500, ge=300, le=20000)


class WebResearch(Tool):
    name = "web.research"
    description = ("Research a topic in one step: web search, then fetch and extract the top sources. Returns excerpts "
                   "with their URLs and retrieval times; these URLs become citable. Use for 'research X', 'find information "
                   "about X', then write the summary with documents.write and list the sources.")
    input_model = ResearchInput
    capabilities = ("web.search", "web.fetch")
    categories = ("web", "research", "documents")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 180.0

    def progress_line(self, args: ResearchInput) -> str | None:
        return "Researching."

    async def run(self, args: ResearchInput, ctx: ToolContext) -> ToolResult:
        try:
            provider, hits = await ctx.services.search.search(args.query, max(args.max_sources * 2, 5))
        except AllProvidersFailed as exc:
            raise CapabilityUnavailable("web search is unavailable (no search provider reachable)",
                                        "docs/providers.md#search", str(exc)) from exc
        sources: list[dict[str, Any]] = []
        failures: list[str] = []
        log = ctx.services.extras.setdefault("fetch_log", {}).setdefault(ctx.task_id, [])
        for hit in hits:
            if len(sources) >= args.max_sources:
                break
            if not hit.url.startswith(("http://", "https://")):
                continue
            host = (urlparse(hit.url).hostname or "").lower()
            if _is_local_host(host) and not (ctx.scope is not None and ctx.scope.mentions(host)):
                # a search result pointing into the local network (router, NAS, metadata service) is not research
                failures.append(f"{hit.url}: local-network address skipped")
                continue
            try:
                page = await fetch_url(hit.url)
            except ToolError as exc:
                failures.append(f"{hit.url}: {exc}")
                continue
            text = (page.get("text") or "").strip()
            if len(text) < 200:
                failures.append(f"{hit.url}: too little text")
                continue
            log.extend([hit.url, page["url"]])
            sources.append({"title": page.get("title") or hit.title, "url": page["url"], "retrieved_at": page["retrieved_at"],
                            "excerpt": text[: args.chars_per_source]})
        del log[:-400]
        if not sources:
            raise ToolError("no source could be fetched: " + "; ".join(failures[:4]), "NoSources")
        view = "\n\n".join(f"[{i + 1}] {s['title']}\n{s['url']} (retrieved {s['retrieved_at']})\n{s['excerpt']}"
                            for i, s in enumerate(sources))
        return self.ok(f"Read {len(sources)} sources about '{args.query}'", {"provider": provider, "sources": sources,
                                                                            "failed": failures},
                       model_view=view, source=f"research:{args.query[:60]}")


TOOLS: list[type[Tool]] = [WebSearch, WebFetch, WebResearch]
