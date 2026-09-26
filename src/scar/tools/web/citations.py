"""Citation verification: every cited URL must have been fetched in this task (no fabricated sources)."""

from __future__ import annotations

import json
import re
from typing import Any

URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")
FETCH_TOOLS = ("web.fetch", "browser.open", "browser.extract", "browser.navigate", "browser.click", "web.research")


def _norm(url: str) -> str:
    url = url.rstrip(".,;:")
    url = re.sub(r"#.*$", "", url)
    return url.rstrip("/").lower().replace("http://", "https://").replace("://www.", "://")


def fetched_urls(db: Any, task_id: str, extra: list[str] | None = None) -> set[str]:
    """URLs successfully loaded by fetch/browse tools in this task (from the persisted tool log)."""
    rows = db.query("SELECT tool, args_json FROM tool_calls WHERE task_id = ? AND status = 'ok' AND tool IN "
                    f"({','.join('?' * len(FETCH_TOOLS))})", (task_id, *FETCH_TOOLS))
    urls: set[str] = set()
    for r in rows:
        try:
            args = json.loads(r["args_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(args, dict) and isinstance(args.get("url"), str):
            urls.add(_norm(args["url"]))
    for u in extra or []:
        urls.add(_norm(u))
    return urls


def check_citations(text: str, allowed: set[str]) -> tuple[list[str], list[str]]:
    """Return (verified_urls, unverified_urls) cited in ``text``."""
    cited = [u.rstrip(".,;:") for u in URL_RE.findall(text)]
    ok: list[str] = []
    bad: list[str] = []
    for u in dict.fromkeys(cited):
        (ok if _norm(u) in allowed else bad).append(u)
    return ok, bad
