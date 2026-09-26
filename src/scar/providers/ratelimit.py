"""Rate-limit header parsing (Retry-After, x-ratelimit-*)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(value: str) -> float | None:
    """Parse '7.66s', '2m59.56s', '1h2m', '250ms', or plain seconds."""
    v = value.strip().lower()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    m = _DURATION.match(v)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def parse_retry_after(headers: Mapping[str, str], body: str = "") -> float | None:
    """Best-effort seconds until the provider accepts requests again."""
    lower = {k.lower(): v for k, v in headers.items()}
    if "retry-after-ms" in lower:
        try:
            return float(lower["retry-after-ms"]) / 1000.0
        except ValueError:
            pass
    ra = lower.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            try:
                when = parsedate_to_datetime(ra)
                return max(0.0, (when - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError):
                pass
    candidates: list[float] = []
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens", "x-ratelimit-reset", "ratelimit-reset"):
        if key in lower:
            d = parse_duration(lower[key])
            if d is not None:
                # some providers send an epoch timestamp
                if d > 1_000_000_000:
                    d = max(0.0, d - datetime.now(UTC).timestamp())
                candidates.append(d)
    remaining = {k: lower.get(k) for k in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens")}
    if candidates:
        # only the exhausted dimension matters; if unknown, take the max
        if remaining.get("x-ratelimit-remaining-requests") == "0" and "x-ratelimit-reset-requests" in lower:
            return parse_duration(lower["x-ratelimit-reset-requests"])
        if remaining.get("x-ratelimit-remaining-tokens") == "0" and "x-ratelimit-reset-tokens" in lower:
            return parse_duration(lower["x-ratelimit-reset-tokens"])
        return max(candidates)
    # Gemini: "retryDelay": "42s" in the error body
    m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', body)
    if m:
        return float(m.group(1))
    m = re.search(r"(?i)try again in ([\dhms.]+)", body)
    if m:
        return parse_duration(m.group(1))
    return None
