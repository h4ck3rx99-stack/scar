"""Scar's own settings, changeable by voice ("set your follow-up time to 10 seconds", "be funnier",
"make yourself bigger", "turn off the pranks") or from the Settings panel on the robot's right-click menu.

Saved in %APPDATA%\\jarvis\\settings.json and applied live. Backend settings change the brain directly;
frontend ones (size, pranks, sounds...) are pushed to the body with the `settings` event."""
import logging
import re

from jarvis import config, runtime
from jarvis.skills.store import Store

log = logging.getLogger("settings")
_store = Store("settings", {})

# key: kind, default, range/choices, side, spoken label, unit, extra spoken names
SPEC = {
    "follow_up_seconds": {"kind": "number", "default": config.FOLLOW_UP_MS / 1000, "min": 2, "max": 30, "side": "backend",
                          "label": "follow-up time", "unit": "seconds",
                          "names": ["follow up", "follow up time", "follow up window", "listening time", "listen time", "wait time"]},
    "sleep_minutes": {"kind": "number", "default": config.SLEEP_AFTER_S / 60, "min": 1, "max": 120, "side": "backend",
                      "label": "nap time", "unit": "minutes",
                      "names": ["sleep time", "sleep timer", "nap time", "idle time", "sleep delay", "time before sleeping"]},
    "always_listen": {"kind": "bool", "default": config.ALWAYS_LISTEN, "side": "backend", "label": "always listening",
                      "names": ["always listen", "always listening", "always on listening", "ambient listening"]},
    "only_my_voice": {"kind": "bool", "default": config.ONLY_MY_VOICE, "side": "backend", "label": "only my voice",
                      "names": ["only my voice", "voice lock", "voice filter"]},
    "suggestions": {"kind": "bool", "default": True, "side": "backend", "label": "suggestions",
                    "names": ["suggestions", "tips", "proactive help", "proactive mode", "break reminders"]},
    "voice_speed": {"kind": "number", "default": config.KOKORO_SPEED, "min": 0.7, "max": 1.6, "side": "backend",
                    "label": "speaking speed", "unit": "x",
                    "names": ["voice speed", "speaking speed", "speech speed", "talking speed", "speech rate"]},
    "voice_volume": {"kind": "number", "default": 100, "min": 20, "max": 200, "side": "backend", "label": "voice volume",
                     "unit": "percent", "names": ["voice volume", "your volume", "speaking volume", "voice loudness"]},
    "personality": {"kind": "choice", "default": "normal", "side": "backend", "label": "personality",
                    "choices": ["normal", "funny", "formal", "brief", "detailed", "sarcastic", "calm"],
                    "names": ["personality", "tone", "style", "mood", "answer style", "attitude"]},
    "city": {"kind": "text", "default": "", "side": "backend", "label": "home city",
             "names": ["city", "home city", "location", "weather city", "my city"]},
    "size": {"kind": "number", "default": 1.0, "min": 0.5, "max": 2.0, "side": "frontend", "label": "size", "unit": "x",
             "names": ["size", "your size", "robot size", "scale"]},
    "pranks": {"kind": "bool", "default": True, "side": "frontend", "label": "playful pranks",
               "names": ["pranks", "playful mode", "playful pranks", "mischief", "playfulness"]},
    "idle_moves": {"kind": "bool", "default": True, "side": "frontend", "label": "idle moves",
                   "names": ["idle moves", "idle animations", "fidgeting", "random moves", "animations"]},
    "roam_speed": {"kind": "number", "default": 1.0, "min": 0.3, "max": 2.0, "side": "frontend", "label": "roaming speed",
                   "unit": "x", "names": ["roam speed", "roaming speed", "flying speed", "movement speed", "speed"]},
    "sounds": {"kind": "bool", "default": True, "side": "frontend", "label": "sound effects",
               "names": ["sounds", "sound effects", "beeps", "effects"]},
    "bubbles": {"kind": "bool", "default": True, "side": "frontend", "label": "speech bubbles",
                "names": ["bubbles", "speech bubbles", "captions", "subtitles", "text bubbles"]},
}
FRONTEND_KEYS = [k for k, s in SPEC.items() if s["side"] == "frontend"]
_NAMES = {n: k for k, s in SPEC.items() for n in [k.replace("_", " "), *s["names"]]}
PERSONALITY = {
    "normal": "",
    "funny": "Personality: playful and funny; add a light joke or witty remark when it fits, but still answer.",
    "formal": "Personality: formal and polished, like a butler. Address the user as 'sir' or 'ma'am' sparingly.",
    "brief": "Personality: extremely brief. One short sentence whenever possible.",
    "detailed": "Personality: thorough. Give fuller explanations (up to 5 sentences) when a question deserves it.",
    "sarcastic": "Personality: dry, sarcastic wit (friendly, never mean), still genuinely helpful.",
    "calm": "Personality: calm, gentle and reassuring.",
}
_hooks = {}   # key -> fn(value), registered by whoever owns the live object (session, speaker, ...)


def on_change(key, fn):
    _hooks[key] = fn


def values() -> dict:
    saved = _store.load()
    return {k: saved.get(k, s["default"]) for k, s in SPEC.items()}


def get(key):
    return values()[key]


def frontend_values() -> dict:
    v = values()
    return {k: v[k] for k in FRONTEND_KEYS}


def resolve_key(name: str):
    """Spoken setting name -> key ("follow up time" -> follow_up_seconds). None if unknown."""
    n = re.sub(r"[^\w\s]", " ", (name or "").lower().replace("-", " "))
    n = re.sub(r"\b(?:your|the|my|of|setting|settings)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if n in SPEC:
        return n
    if n in _NAMES:
        return _NAMES[n]
    from rapidfuzz import fuzz, process
    m = process.extractOne(n, _NAMES.keys(), scorer=fuzz.ratio, score_cutoff=82)
    return _NAMES[m[0]] if m else None


_ON = {"on", "yes", "true", "enable", "enabled", "start", "allow", "1"}
_OFF = {"off", "no", "false", "disable", "disabled", "stop", "none", "0"}
_REL = {"up": 1, "higher": 1, "more": 1, "bigger": 1, "larger": 1, "faster": 1, "louder": 1, "longer": 1,
        "down": -1, "lower": -1, "less": -1, "smaller": -1, "slower": -1, "quieter": -1, "softer": -1, "shorter": -1}
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30, "half": 0.5, "double": 2, "normal": None, "default": None}


def parse_value(key, raw):
    """Spoken value -> typed value for this setting. Returns (value, error)."""
    s = SPEC[key]
    cur = get(key)
    r = str(raw).lower().strip().rstrip(".")
    if r in ("default", "normal", "reset", "the default") and s["kind"] != "choice":
        return s["default"], None
    if s["kind"] == "bool":
        if r in _ON or r.startswith(("on", "enable")):
            return True, None
        if r in _OFF or r.startswith(("off", "disable")):
            return False, None
        return None, f"{s['label']} can only be on or off"
    if s["kind"] == "choice":
        r = {"funnier": "funny", "serious": "formal", "professional": "formal", "short": "brief", "shorter": "brief",
             "concise": "brief", "long": "detailed", "longer": "detailed", "default": "normal", "regular": "normal",
             "polite": "formal", "relaxed": "calm", "chill": "calm"}.get(r, r)
        if r in s["choices"]:
            return r, None
        return None, f"{s['label']} can be " + ", ".join(s["choices"])
    if s["kind"] == "text":
        return str(raw).strip().title(), None
    # number: absolute ("10 seconds", "150 percent", "double"), or relative ("bigger", "up")
    lo, hi = s["min"], s["max"]
    step = (hi - lo) / 8
    if r in _REL:
        return round(max(lo, min(hi, cur + _REL[r] * step)), 2), None
    if r in ("double", "twice"):
        return round(max(lo, min(hi, cur * 2)), 2), None
    if r in ("half", "halve"):
        return round(max(lo, min(hi, cur / 2)), 2), None
    m = re.search(r"(\d+(?:\.\d+)?)", r)
    n = float(m.group(1)) if m else _WORDNUM.get(r.split()[0] if r.split() else "")
    if n is None:
        return None, f"I need a number for {s['label']}"
    unit = s.get("unit")
    if unit == "minutes" and re.search(r"\bsec", r):
        n /= 60
    elif unit == "seconds" and re.search(r"\bmin", r):
        n *= 60
    elif unit == "x" and re.search(r"%|percent", r):
        n /= 100
    elif unit == "percent" and n <= 3 and not re.search(r"%|percent", r):
        n *= 100                     # "voice volume 1.5" = 150 %
    if not lo <= n <= hi:
        return None, f"{s['label']} goes from {lo:g} to {hi:g} {unit if unit != 'x' else 'times'}"
    return round(n, 2), None


def set_value(key, value):
    data = _store.load()
    data[key] = value
    _store.save(data)
    apply(key, value)


def reset():
    _store.save({})
    for k, v in values().items():
        apply(k, v)


def apply(key, value):
    """Push one value into the live system."""
    try:
        if key == "follow_up_seconds":
            config.FOLLOW_UP_MS = int(value * 1000)
        elif key == "sleep_minutes":
            config.SLEEP_AFTER_S = int(value * 60)
        elif key == "voice_speed" and runtime.tts is not None:
            runtime.tts.speed = float(value)
        elif key == "city":
            from jarvis.skills import web
            web._location = None           # re-resolve the weather location next time
        elif key == "suggestions":
            from jarvis.core.proactive import prefs
            prefs.save({**prefs.load(), "suggestions": bool(value)})
        if key in _hooks:
            _hooks[key](value)
    except Exception:  # noqa: BLE001 - a bad saved value must never stop Scar
        log.exception("could not apply setting %s=%r", key, value)


def apply_all():
    saved = _store.load()
    for k, v in values().items():
        if k in saved:
            apply(k, v)


def describe(key, value=None):
    s = SPEC[key]
    v = get(key) if value is None else value
    if s["kind"] == "bool":
        return f"{s['label']} is {'on' if v else 'off'}"
    if s["kind"] == "number":
        unit = {"x": "times", "percent": "percent"}.get(s.get("unit"), s.get("unit", ""))
        return f"{s['label']} is {v:g} {unit}".strip()
    return f"{s['label']} is {v or 'not set'}"


def summary() -> str:
    return "; ".join(describe(k) for k in SPEC) + "."
