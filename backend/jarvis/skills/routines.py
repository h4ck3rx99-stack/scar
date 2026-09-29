"""Routines: a name -> a list of spoken commands, run one after another through the normal command pipeline.

"create a routine called study mode: close youtube, open vs code, start a 50 minute timer"
"study mode"   /   "run study mode"   /   "start my morning routine"
"""
import re

from rapidfuzz import fuzz, process

from jarvis.skills.store import Store

_store = Store("routines", {})

BUILT_IN = {
    "study mode": ["close youtube", "close instagram", "open vs code", "set a timer for 50 minutes", "volume 30"],
    "break time": ["set a timer for 10 minutes", "open youtube"],
    "focus mode": ["minimize everything", "set a timer for 25 minutes", "mute"],
    "good night": ["what are my reminders", "set brightness to 20", "go on standby"],
}


def all_routines():
    return {**BUILT_IN, **_store.load()}


_VERBS = r"(?:open|close|start|set|play|pause|search|minimize|maximize|mute|unmute|turn|go|launch|lock|take|remind|volume|brightness|snap|move|switch|run|say|show)"


def split_steps(text: str):
    parts = re.split(rf",\s*(?:and\s+|then\s+)?|\s+(?:and then|then|after that)\s+|;\s*|\s+and\s+(?={_VERBS}\b)", text)
    out = []
    for p in parts:
        # Speech often arrives without commas: "close vs code open steam volume 80".
        out += re.split(rf"(?<!\bto)(?<!\bthe)(?<!\bmy)(?<!\bthe screen)\s+(?={_VERBS}\b)", p)
    return [p.strip(" .") for p in out if p and p.strip(" .")]


def create(name: str, steps_text: str):
    name = name.lower().strip(" .")
    steps = split_steps(steps_text)
    if not steps:
        return None
    data = _store.load()
    data[name] = steps
    _store.save(data)
    return name, steps


def delete(name: str):
    data = _store.load()
    hit = process.extractOne(name.lower(), data.keys(), scorer=fuzz.WRatio, score_cutoff=80)
    if not hit:
        return None
    data.pop(hit[0])
    _store.save(data)
    return hit[0]


def find(name: str):
    routines = all_routines()
    q = name.lower().replace("routine", "").replace("my ", "").strip()
    hit = process.extractOne(q, routines.keys(), scorer=fuzz.WRatio, score_cutoff=85)
    return (hit[0], routines[hit[0]]) if hit else None
