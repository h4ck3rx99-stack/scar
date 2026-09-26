"""Voice approval phrases (C4.5). Voice goes through the same approval broker; it never bypasses policy.

* only within the listening window after SCAR asks, and only for the current pending request
* CRITICAL actions are never approved by voice alone
* persistent ("always") permissions require a spoken read-back confirmation
"""

from __future__ import annotations

import re

from scar.security.approval import ApprovalResponse

_PATTERNS: list[tuple[str, ApprovalResponse]] = [
    (r"\b(always|never) (deny|block|refuse)\b|\bnever allow\b|\bdon'?t ever\b", ApprovalResponse.DENY_ALWAYS),
    (r"\b(deny|no|nope|don'?t|do not|cancel|stop|reject|refuse|block)\b", ApprovalResponse.DENY),
    (r"\balways (allow|approve)\b|\ballow always\b|\bpermanent(ly)?\b", ApprovalResponse.ALLOW_ALWAYS),
    (r"\b(for (this|the) session|for today|temporar(y|ily)|for now)\b", ApprovalResponse.ALLOW_SESSION),
    (r"\b(for (this|the) task|for this job)\b", ApprovalResponse.ALLOW_TASK),
    (r"\b(for an hour|for (60|sixty) minutes)\b", ApprovalResponse.ALLOW_TIMED),
    (r"\b(allow|approve|approved|yes|yeah|yep|go ahead|do it|send it|ok(ay)?|confirm(ed)?|proceed|sure)\b",
     ApprovalResponse.ALLOW_ONCE),
]

CONFIRM = re.compile(r"(?i)\b(yes|confirm(ed)?|correct|that'?s right|affirmative)\b")


def parse_approval(text: str) -> ApprovalResponse | None:
    t = text.lower().strip()
    if not t:
        return None
    for pattern, response in _PATTERNS:
        if re.search(pattern, t):
            return response
    return None


def is_stop_command(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)\W*(stop|cancel|abort|halt|shut up|be quiet|never ?mind)\W*(it|that|everything|now)?\W*", text.strip()))


def is_revoke_command(text: str) -> bool:
    return bool(re.search(r"(?i)\b(revoke|remove|clear|cancel) (all )?(my )?(permissions|grants)\b", text))
