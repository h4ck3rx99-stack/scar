"""Main-content extraction from HTML (trafilatura), with a plain-text fallback."""

from __future__ import annotations

import html as htmllib
import re


def extract_main(html: str, url: str | None = None) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True,
                                   output_format="txt")
        if text and len(text.strip()) > 80:
            return text.strip()
    except Exception:  # noqa: BLE001 - trafilatura can raise on malformed markup
        pass
    return html_to_text(html)


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|template|svg).*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def title_of(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return htmllib.unescape(m.group(1)).strip()[:200] if m else ""


def hidden_text_flags(html: str) -> list[str]:
    """Detect text hidden from humans but visible to machines (a classic injection carrier)."""
    flags = []
    if re.search(r"(?is)<[^>]+style\s*=\s*['\"][^'\"]*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|"
                 r"opacity\s*:\s*0)[^'\"]*['\"][^>]*>[^<]{20,}", html):
        flags.append("hidden_html_text")
    if re.search(r"(?is)<[^>]+aria-hidden\s*=\s*['\"]true['\"][^>]*>[^<]{40,}", html):
        flags.append("aria_hidden_text")
    return flags
