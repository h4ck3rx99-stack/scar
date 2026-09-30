"""Clipboard history: "what did I copy earlier?", "paste what I copied before", "paste the link I copied".

Kept in memory only (never written to disk), last 40 text items. Anything that looks like a password, key or token
is skipped, and so is what Scar itself pastes while typing for you."""
import ctypes
import re
import threading
import time

user32 = ctypes.windll.user32
MAX_ITEMS = 40
_items = []            # newest last: {"text", "at"}
_lock = threading.Lock()
enabled = True
# Tokens and keys: long runs without spaces mixing letters and digits, or well-known prefixes.
_SECRET = re.compile(r"^(?:sk-|sk_|pk_|ghp_|gho_|github_pat_|xox[bap]-|AKIA|AIza|ya29\.|eyJ)[\w\-.]{8,}|^(?=\S*\d)(?=\S*[A-Za-z])\S{24,}$")
_PASSWORDISH = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^\w\s])\S{8,40}$")


def looks_secret(text: str) -> bool:
    t = text.strip()
    if "\n" in t or " " in t and len(t) > 40:
        return False                 # prose and code aren't secrets (keys and passwords are single tokens)
    return bool(_SECRET.match(t) or _PASSWORDISH.match(t))


def _add(text: str):
    from jarvis.skills.screen import PASTING
    t = text.strip()
    if not t or len(t) > 20000 or t == (PASTING.get("text") or "").strip() or looks_secret(t):
        return
    with _lock:
        _items[:] = [i for i in _items if i["text"] != t]
        _items.append({"text": t, "at": time.time()})
        del _items[:-MAX_ITEMS]


def watch():
    """Background thread: notice every new clipboard text (the sequence number changes on each copy)."""
    from jarvis.skills.screen import get_clipboard
    last = user32.GetClipboardSequenceNumber()
    while True:
        time.sleep(0.6)
        seq = user32.GetClipboardSequenceNumber()
        if seq == last or not enabled:
            last = seq
            continue
        last = seq
        try:
            _add(get_clipboard())
        except Exception:  # noqa: BLE001 - another app holding the clipboard: try on the next change
            pass


def start():
    threading.Thread(target=watch, daemon=True, name="clipboard-history").start()


def items():
    with _lock:
        return list(reversed(_items))        # newest first


def clear():
    with _lock:
        _items.clear()


_ORD = {"last": 0, "latest": 0, "previous": 1, "before": 1, "earlier": 1, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
        "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}


def pick(which: str | None = None, query: str | None = None):
    """The item the user means: by position ("previous", "third") or by content ("the link", "about invoice")."""
    hist = items()
    if not hist:
        return None
    if query:
        q = query.lower().strip()
        if q in ("link", "url", "the link", "the url"):
            hit = next((i for i in hist if re.match(r"https?://", i["text"])), None)
            if hit:
                return hit
        if q in ("email", "the email", "email address"):
            hit = next((i for i in hist if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", i["text"])), None)
            if hit:
                return hit
        from rapidfuzz import fuzz
        scored = sorted(hist, key=lambda i: fuzz.partial_ratio(q, i["text"].lower()), reverse=True)
        return scored[0] if fuzz.partial_ratio(q, scored[0]["text"].lower()) >= 70 else None
    idx = _ORD.get((which or "previous").lower().split()[0], 1)
    return hist[min(idx, len(hist) - 1)]
