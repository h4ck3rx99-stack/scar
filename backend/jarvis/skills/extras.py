"""Handy extras: stopwatch, coin/dice/random, unit + currency conversion, news headlines, dictionary, internet check.
Online pieces use keyless public services (Frankfurter rates, Google News RSS, dictionaryapi.dev, ipify)."""
import html
import random
import re
import time
import xml.etree.ElementTree as ET

import httpx

from jarvis.skills.web import OFFLINE

_client = httpx.AsyncClient(timeout=httpx.Timeout(8, connect=6), follow_redirects=True,
                            headers={"User-Agent": "JarvisDesktopAssistant/2.0 (personal desktop assistant; https://example.org/jarvis)"})

# ------------------------------------------------------------------ stopwatch
_sw = {"start": None, "laps": []}


def fmt_duration(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = ([f"{h} hour{'s' * (h != 1)}"] if h else []) + ([f"{m} minute{'s' * (m != 1)}"] if m else []) + \
            ([f"{sec} second{'s' * (sec != 1)}"] if sec or not (h or m) else [])
    return " ".join(parts)


def stopwatch(action: str) -> str:
    now = time.time()
    if action == "start":
        _sw.update(start=now, laps=[])
        return "Stopwatch started."
    if _sw["start"] is None:
        return "The stopwatch isn't running."
    elapsed = now - _sw["start"]
    if action == "lap":
        _sw["laps"].append(elapsed)
        return f"Lap {len(_sw['laps'])}: {fmt_duration(elapsed)}."
    if action == "stop":
        _sw["start"] = None
        return f"Stopped at {fmt_duration(elapsed)}."
    return f"{fmt_duration(elapsed)} so far."


# ------------------------------------------------------------------ chance
def chance(kind: str, low: int = 1, high: int = 100) -> str:
    if kind == "coin":
        return random.choice(["Heads.", "Tails."])
    if kind == "dice":
        return f"You rolled a {random.randint(1, 6)}."
    low, high = min(low, high), max(low, high)
    return f"{random.randint(low, high)}."


# ------------------------------------------------------------------ conversion
_UNITS = {  # unit -> (dimension, factor to SI base)
    "mm": ("len", 1e-3), "millimeter": ("len", 1e-3), "cm": ("len", 1e-2), "centimeter": ("len", 1e-2),
    "m": ("len", 1), "meter": ("len", 1), "metre": ("len", 1), "km": ("len", 1e3), "kilometer": ("len", 1e3),
    "kilometre": ("len", 1e3), "in": ("len", 0.0254), "inch": ("len", 0.0254), "ft": ("len", 0.3048),
    "foot": ("len", 0.3048), "feet": ("len", 0.3048), "yard": ("len", 0.9144), "mile": ("len", 1609.344),
    "g": ("mass", 1e-3), "gram": ("mass", 1e-3), "kg": ("mass", 1), "kilogram": ("mass", 1), "kilo": ("mass", 1),
    "lb": ("mass", 0.45359237), "pound": ("mass", 0.45359237), "ounce": ("mass", 0.028349523), "oz": ("mass", 0.028349523),
    "tonne": ("mass", 1000), "ton": ("mass", 1000),
    "ml": ("vol", 1e-3), "milliliter": ("vol", 1e-3), "l": ("vol", 1), "liter": ("vol", 1), "litre": ("vol", 1),
    "gallon": ("vol", 3.785411784), "cup": ("vol", 0.2365882365),
    "kmh": ("speed", 1 / 3.6), "km/h": ("speed", 1 / 3.6), "kph": ("speed", 1 / 3.6), "mph": ("speed", 0.44704),
    "m/s": ("speed", 1), "knot": ("speed", 0.514444),
    "byte": ("data", 1), "kb": ("data", 1e3), "mb": ("data", 1e6), "gb": ("data", 1e9), "tb": ("data", 1e12),
    "kilobyte": ("data", 1e3), "megabyte": ("data", 1e6), "gigabyte": ("data", 1e9), "terabyte": ("data", 1e12),
    "second": ("time", 1), "minute": ("time", 60), "hour": ("time", 3600), "day": ("time", 86400), "week": ("time", 604800),
}
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
_CURRENCY = {"rupee": "INR", "rupees": "INR", "inr": "INR", "rs": "INR", "dollar": "USD", "dollars": "USD", "usd": "USD",
             "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound sterling": "GBP", "gbp": "GBP", "yen": "JPY", "jpy": "JPY",
             "dirham": "AED", "aed": "AED", "yuan": "CNY", "cny": "CNY", "canadian dollar": "CAD", "cad": "CAD",
             "australian dollar": "AUD", "aud": "AUD", "singapore dollar": "SGD", "sgd": "SGD"}


def _norm_unit(u: str) -> str:
    u = u.lower().strip().rstrip(".")
    if u in _UNITS or u in _TEMP or u in _CURRENCY:
        return u
    if u.endswith("es") and u[:-2] in _UNITS:
        return u[:-2]
    if u.endswith("s") and (u[:-1] in _UNITS or u[:-1] in _TEMP or u[:-1] in _CURRENCY):
        return u[:-1]
    return u


async def convert(amount: float, frm: str, to: str) -> str:
    f, t = _norm_unit(frm), _norm_unit(to)
    if f in _TEMP and t in _TEMP:
        a, b = _TEMP[f], _TEMP[t]
        c = amount if a == "c" else (amount - 32) * 5 / 9 if a == "f" else amount - 273.15
        out = c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15
        return f"{amount:g} {frm} is {out:.1f} {to}."
    if f in _UNITS and t in _UNITS:
        (d1, k1), (d2, k2) = _UNITS[f], _UNITS[t]
        if d1 != d2:
            return f"I can't convert {frm} into {to}."
        out = amount * k1 / k2
        return f"{amount:g} {frm} is {out:,.4g} {to}."
    if f in _CURRENCY and t in _CURRENCY:
        if OFFLINE:
            return "Currency rates need the internet."
        try:
            r = await _client.get("https://api.frankfurter.app/latest", params={"amount": amount, "from": _CURRENCY[f], "to": _CURRENCY[t]})
            rate = r.json()["rates"][_CURRENCY[t]]
            return f"{amount:g} {_CURRENCY[f]} is about {rate:,.2f} {_CURRENCY[t]} (today's rate)."
        except (httpx.HTTPError, ValueError, KeyError):
            return "I couldn't reach the currency-rate service just now."
    return f"I don't know how to convert {frm} to {to}."


# ------------------------------------------------------------------ news
async def headlines(topic: str | None = None, n: int = 5):
    if OFFLINE:
        return None
    url = "https://news.google.com/rss" + ("/search" if topic else "")
    params = {"hl": "en-IN", "gl": "IN", "ceid": "IN:en", **({"q": topic} if topic else {})}
    try:
        r = await _client.get(url, params=params)
        root = ET.fromstring(r.text)
    except (httpx.HTTPError, ET.ParseError):
        return None
    out = []
    for item in root.iter("item"):
        title = html.unescape(item.findtext("title") or "")
        title = re.sub(r"\s+-\s+[^-]+$", "", title)   # drop " - Source"
        if title:
            out.append(title)
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ dictionary
async def define(word: str):
    if OFFLINE:
        return None
    try:
        r = await _client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word.strip().lower()}")
        if r.status_code != 200:
            return None
        entry = r.json()[0]
        meanings = entry.get("meanings", [])[:2]
        return "; ".join(f"({m['partOfSpeech']}) {m['definitions'][0]['definition']}" for m in meanings if m.get("definitions"))
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


# ------------------------------------------------------------------ internet
async def internet_check():
    t = time.perf_counter()
    try:
        r = await _client.get("https://api.ipify.org", params={"format": "json"})
        ms = (time.perf_counter() - t) * 1000
        return f"Online. Public IP {r.json()['ip']}, round trip {ms:.0f} ms."
    except (httpx.HTTPError, ValueError, KeyError):
        return "Looks like there's no internet connection right now."
