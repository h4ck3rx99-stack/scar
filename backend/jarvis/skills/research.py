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


_ddg_blocked_until = 0.0
_STOP = {"the", "and", "for", "with", "under", "best", "top", "what", "how", "why", "are", "is", "of", "in", "on", "to", "a", "an", "vs"}


def _relevant(results, query):
    """Drop results that share nothing but a generic word with the query ("best laptops" -> BEST bus company)."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _STOP and not w.isdigit()}
    if not words:
        return results
    need = 1 if len(words) <= 3 else 2
    keep = []
    for r in results:
        text = (r["title"] + " " + r["snippet"] + " " + r["url"]).lower()
        if sum(1 for w in words if w in text or w.rstrip("s") in text) >= need:
            keep.append(r)
    return keep


async def search(query: str, n: int = 6):
    """[{title, url, snippet}] for a web query: DuckDuckGo, falling back to Bing; irrelevant hits dropped."""
    import time
    global _ddg_blocked_until
    if OFFLINE:
        return []
    results = []
    if time.time() > _ddg_blocked_until:
        results = await _ddg(query, n)
        if results is None:            # bot check: leave DuckDuckGo alone for a while
            _ddg_blocked_until = time.time() + 1800
            results = []
    results = _relevant(results or [], query)
    if not results:
        results = _relevant(await _bing(query, n), query)
    return results[:n]


async def _bing(query: str, n: int):
    import base64
    try:
        r = await _client.get("https://www.bing.com/search", params={"q": query, "form": "QBLH"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("bing failed: %s %s", type(e).__name__, e)
        return []
    out = []
    for item in re.findall(r'<li class="b_algo".*?</li>', r.text, re.S):
        m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not m:
            continue
        href = htmllib.unescape(m.group(1))
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):   # Bing wraps links as /ck/a?...&u=a1<base64url of the real URL>
            try:
                href = base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode(errors="ignore")
            except ValueError:
                pass
        snip = re.search(r"<p[^>]*>(.*?)</p>", item, re.S)
        out.append({"title": _strip(m.group(2)), "url": href, "snippet": _strip(snip.group(1)) if snip else ""})
        if len(out) >= n:
            break
    return out


async def _ddg(query: str, n: int):
    """DuckDuckGo HTML results, [] on network failure, None when it answers with a bot check."""
    try:
        r = await _client.get("https://html.duckduckgo.com/html/", params={"q": query})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("search failed: %s %s", type(e).__name__, e)
        return []
    if r.status_code == 202 or "anomaly" in r.text[:5000].lower():
        log.warning("DuckDuckGo bot check; using Bing for 30 minutes")
        return None
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
    results = []
    for q in (query, f"{query} review", f"{query} 2026 list"):   # rephrase before giving up
        results = await search(q)
        if results:
            break
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
