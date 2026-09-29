"""Keyless web research: DuckDuckGo's HTML results + readable text from the top pages."""
import asyncio
import html as htmllib
import logging
import re
import urllib.parse

import httpx

from jarvis.skills.web import OFFLINE

log = logging.getLogger("research")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
       "Accept-Language": "en-IN,en;q=0.9"}
_client = httpx.AsyncClient(timeout=httpx.Timeout(10, connect=6), follow_redirects=True, headers=_UA)


def _unwrap(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<real url>."""
    if "uddg=" in href:
        return urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0])
    return href if href.startswith("http") else "https:" + href


def _strip(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()


async def search(query: str, n: int = 6):
    """[{title, url, snippet}] for a web query."""
    if OFFLINE:
        return []
    try:
        r = await _client.get("https://html.duckduckgo.com/html/", params={"q": query})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("search failed: %s %s", type(e).__name__, e)
        return []
    out = []
    blocks = re.split(r'<div class="result results_links', r.text)[1:]
    for b in blocks:
        m = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        url = _unwrap(htmllib.unescape(m.group(1)))
        if "duckduckgo.com/y.js" in url or "ad_domain" in url:   # sponsored
            continue
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        out.append({"title": _strip(m.group(2)), "url": url, "snippet": _strip(snip.group(1)) if snip else ""})
        if len(out) >= n:
            break
    return out


def readable_text(html: str, limit=2500) -> str:
    """Crude article extraction: drop scripts/styles/nav, keep paragraph-ish text."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|aside)[^>]*>.*?</\1>", " ", html)
    paras = re.findall(r"(?is)<(?:p|li|h1|h2|h3|td)[^>]*>(.*?)</(?:p|li|h1|h2|h3|td)>", html)
    text = "\n".join(t for t in (_strip(p) for p in paras) if len(t) > 40)
    if len(text) < 300:
        text = _strip(html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text[:limit]


async def fetch(url: str, limit=2500) -> str:
    try:
        r = await _client.get(url)
        if "text/html" not in r.headers.get("content-type", "") and "text/plain" not in r.headers.get("content-type", ""):
            return ""
        return readable_text(r.text, limit)
    except httpx.HTTPError as e:
        log.info("fetch %s failed: %s", url[:80], type(e).__name__)
        return ""


async def research(query: str, pages: int = 3) -> str:
    """Search results plus the readable text of the top few pages, ready for the LLM to summarize."""
    results = await search(query)
    if not results:
        return f"Web search for '{query}' returned nothing (search service unreachable or no results)."
    texts = await asyncio.gather(*(fetch(r["url"], 1800) for r in results[:pages]))
    lines = [f"Search results for '{query}':"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']} ({urllib.parse.urlparse(r['url']).netloc}): {r['snippet']}")
    for r, t in zip(results[:pages], texts):
        if t:
            lines.append(f"\n--- From {urllib.parse.urlparse(r['url']).netloc}:\n{t}")
    return "\n".join(lines)[:7000]
