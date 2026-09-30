"""'Watch me do this' macros: record your clicks, typing and scrolling once, then replay them by name.

Saved to %APPDATA%\\jarvis\\macros\\<name>.json on this PC only. Recording stops on "stop recording" (or after
5 minutes); replay can be cut short any time by moving the mouse hard into a screen corner or pressing Escape."""
import json
import re
import threading
import time

from jarvis import config

DIR = config.APPDATA_DIR / "macros"
MAX_S = 300


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "macro"


class Recorder:
    def __init__(self):
        self.events = []
        self.t0 = 0.0
        self.active = False
        self._listeners = []
        self.pending = None      # finished but not yet named

    def start(self):
        from pynput import keyboard, mouse
        self.events, self.t0, self.active, self.pending = [], time.monotonic(), True, None

        def now():
            return round(time.monotonic() - self.t0, 3)

        def on_click(x, y, button, pressed):
            if self.active:
                self.events.append({"t": now(), "type": "click", "x": x, "y": y, "button": button.name, "down": pressed})

        def on_scroll(x, y, dx, dy):
            if self.active:
                self.events.append({"t": now(), "type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

        def key_name(k):
            return {"char": k.char} if getattr(k, "char", None) else {"key": getattr(k, "name", str(k))}

        def on_press(k):
            if self.active:
                self.events.append({"t": now(), "type": "key", "down": True, **key_name(k)})

        def on_release(k):
            if self.active:
                self.events.append({"t": now(), "type": "key", "down": False, **key_name(k)})

        self._listeners = [mouse.Listener(on_click=on_click, on_scroll=on_scroll),
                           keyboard.Listener(on_press=on_press, on_release=on_release)]
        for listener in self._listeners:
            listener.start()
        threading.Timer(MAX_S, self.stop).start()

    def stop(self):
        if not self.active:
            return self.pending
        self.active = False
        for listener in self._listeners:
            listener.stop()
        ev = self.events
        # Drop the push-to-talk hotkey (Ctrl+Shift+Space) the user may have pressed to say "stop recording".
        while ev and ev[-1]["type"] == "key" and ev[-1].get("key") in ("space", "ctrl_l", "ctrl_r", "shift", "shift_r", "ctrl"):
            ev.pop()
        self.pending = ev
        return ev

    def save(self, name: str):
        if not self.pending:
            return None
        DIR.mkdir(parents=True, exist_ok=True)
        path = DIR / f"{_slug(name)}.json"
        path.write_text(json.dumps({"name": name, "events": self.pending}), encoding="utf-8")
        self.pending = None
        return path


recorder = Recorder()


def names():
    if not DIR.is_dir():
        return []
    out = []
    for p in sorted(DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8"))["name"])
        except (OSError, ValueError, KeyError):
            continue
    return out


def find(name: str):
    from rapidfuzz import fuzz, process
    all_names = names()
    m = process.extractOne(name.lower(), [n.lower() for n in all_names], scorer=fuzz.WRatio, score_cutoff=85)
    return all_names[[n.lower() for n in all_names].index(m[0])] if m else None


def delete(name: str) -> bool:
    real = find(name)
    if not real:
        return False
    (DIR / f"{_slug(real)}.json").unlink(missing_ok=True)
    return True


def play(name: str, speed: float = 1.0):
    """Replay a saved macro (blocking; run it in a worker thread). Escape or a mouse slam into the top-left
    corner aborts. Returns the number of events played, or -1 if aborted."""
    from pynput import keyboard, mouse
    real = find(name)
    data = json.loads((DIR / f"{_slug(real)}.json").read_text(encoding="utf-8"))
    kb, ms = keyboard.Controller(), mouse.Controller()
    aborted = threading.Event()

    def on_press(k):
        if k == keyboard.Key.esc:
            aborted.set()
    watcher = keyboard.Listener(on_press=on_press)
    watcher.start()
    start, played = time.monotonic(), 0
    try:
        for e in data["events"]:
            delay = e["t"] / speed - (time.monotonic() - start)
            if delay > 5.0:
                start -= delay - 5.0            # skip long pauses the user spent thinking (keep the rest in rhythm)
                delay = 5.0
            if delay > 0:
                time.sleep(delay)
            if aborted.is_set() or ms.position == (0, 0):
                return -1
            if e["type"] == "click":
                ms.position = (e["x"], e["y"])
                btn = getattr(mouse.Button, e["button"], mouse.Button.left)
                (ms.press if e["down"] else ms.release)(btn)
            elif e["type"] == "scroll":
                ms.position = (e["x"], e["y"])
                ms.scroll(e["dx"], e["dy"])
            elif e["type"] == "key":
                k = e["char"] if "char" in e else getattr(keyboard.Key, e["key"], None)
                if k is None:
                    continue
                (kb.press if e["down"] else kb.release)(k)
            played += 1
    finally:
        watcher.stop()
    return played
