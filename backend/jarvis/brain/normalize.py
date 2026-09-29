"""Cleans up what Whisper heard before it's routed: common mishearings (Indian-English "what's" -> "watch"),
stuttered repeats ("Stand, stand, stand by"), and run-on repeats ("Close this. Close notepad.").
"""
import re

# (pattern, replacement) applied to the lower-cased transcript.
MISHEARINGS = [
    (r"^(?:watch|wats|whats)\s+(?=(?:on|is|the|weather|my|time|date|up|going|happening)\b)", "what's "),
    (r"^watch(?:'s)?\s*$", "what's"),
    (r"\bwatch on (?:my|the) screen\b", "what's on my screen"),
    (r"\bnote ?card\b|\bnote bad\b|\bnot bad\b(?= app)", "notepad"),
    (r"\bstand\W*b(?:uy|ye|y)\b", "standby"),
    (r"\bscreen ?shots\b", "screenshot"),
    (r"\bopen up\b", "open"),
    (r"\bclothes\b(?= (?:this|it|the|notepad|chrome|opera|paint|that|tab|window))", "close"),
    (r"^(?:choose|chose|clues|cloze)\b(?= (?:this|it|the|that))", "close"),
    (r"\byou tube\b", "youtube"),
    (r"\bv s code\b|\bvs code\b|\bvisual code\b", "vs code"),
]


def fix(text: str) -> str:
    t = text.strip()
    low = t.lower()
    for pat, rep in MISHEARINGS:
        low = re.sub(pat, rep, low)
    return low if low != t.lower() else t


def collapse_repeats(text: str) -> str:
    """'Stand, Stand, stand by' -> 'Stand, by' -> handled; 'open open opera' -> 'open opera'."""
    words = text.split()
    out = []
    for w in words:
        if out and re.sub(r"\W", "", w).lower() == re.sub(r"\W", "", out[-1]).lower():
            continue
        out.append(w)
    return " ".join(out)


def sentences(text: str):
    parts = [p.strip() for p in re.split(r"(?<=[.?!])\s+|\s*[;]\s*", text) if p.strip()]
    return parts or [text]


def candidates(text: str):
    """Phrasings to try routing, best first: the whole thing, then each sentence from the last one
    (people repeat themselves more clearly at the end: "Can you close it? Close this Notepad.")."""
    fixed = collapse_repeats(fix(text))
    out = []
    for s in reversed(sentences(fixed)):
        s = collapse_repeats(fix(s))
        if s not in out:
            out.append(s)
    if fixed not in out:
        out.append(fixed)
    return out
