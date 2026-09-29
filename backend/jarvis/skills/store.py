"""Small JSON stores in %APPDATA%\\jarvis: notes, to-do list, facts about the user, reminders."""
import json
import threading
import time

from jarvis import config

_lock = threading.Lock()


class Store:
    def __init__(self, name, default):
        self.path = config.APPDATA_DIR / f"{name}.json"
        self.default = default

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return json.loads(json.dumps(self.default))

    def save(self, data):
        with _lock:
            config.APPDATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)


notes = Store("notes", [])        # [{"text", "at"}]
todos = Store("todos", [])        # [{"text", "done", "at"}]
facts = Store("facts", [])        # ["User's name is Avi", ...]
reminders = Store("reminders", [])  # [{"id", "text", "due", "kind"}]


def add_note(text):
    data = notes.load()
    data.append({"text": text, "at": time.strftime("%Y-%m-%d %H:%M")})
    notes.save(data)
    return len(data)


def add_todo(text):
    data = todos.load()
    data.append({"text": text, "done": False, "at": time.strftime("%Y-%m-%d %H:%M")})
    todos.save(data)
    return sum(1 for t in data if not t["done"])


def complete_todo(query):
    from rapidfuzz import fuzz
    data = todos.load()
    open_items = [t for t in data if not t["done"]]
    if not open_items:
        return None
    best = max(open_items, key=lambda t: fuzz.partial_ratio(query.lower(), t["text"].lower()))
    if fuzz.partial_ratio(query.lower(), best["text"].lower()) < 60:
        return None
    best["done"] = True
    todos.save(data)
    return best["text"]


def clear_done_todos():
    todos.save([t for t in todos.load() if not t["done"]])


def remember(fact):
    data = facts.load()
    if fact not in data:
        data.append(fact)
    facts.save(data[-40:])


def forget(query):
    from rapidfuzz import fuzz
    data = facts.load()
    keep = [f for f in data if fuzz.partial_ratio(query.lower(), f.lower()) < 75]
    facts.save(keep)
    return len(data) - len(keep)
