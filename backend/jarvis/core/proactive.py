"""Background watcher: activity journal, error-dialog help, break suggestions, schedule announcements.

Speaks only through Session.announce, stays silent in standby, and can be switched off by voice
("stop the suggestions") - that preference is remembered.
"""
import asyncio
import ctypes
import datetime
import logging
import re
import time
from ctypes import wintypes

from jarvis import runtime
from jarvis.hands.windows import foreground_window, user32
from jarvis.skills import schedule
from jarvis.skills.store import Store

log = logging.getLogger("proactive")
prefs = Store("proactive", {"suggestions": True})

ERROR_WORDS = re.compile(r"\b(error|exception|failed|failure|fatal|crash(?:ed)?|not responding|stopped working|cannot|can't|unable to|access (?:is )?denied|missing|corrupt)\b", re.I)
BREAK_AFTER_S = 90 * 60
IDLE_RESET_S = 5 * 60


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000


def dialog_text(hwnd) -> str:
    """Text of the static labels inside a classic Windows dialog (#32770)."""
    texts = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(child, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, cls, 64)
        if cls.value in ("Static", "DirectUIHWND", "Edit") and user32.IsWindowVisible(child):
            n = user32.GetWindowTextLengthW(child)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(child, buf, n + 1)
                if buf.value.strip():
                    texts.append(buf.value.strip())
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return " ".join(texts)[:600]


def is_dialog(hwnd) -> bool:
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    return cls.value == "#32770"


class Proactive:
    def __init__(self, session):
        self.session = session
        self.seen_dialogs = set()
        self.active_since = time.time()
        self.break_suggested = False
        self.announced = set()

    @property
    def on(self):
        return prefs.load().get("suggestions", True)

    async def run(self):
        tick = 0
        while True:
            await asyncio.sleep(3)
            tick += 1
            try:
                await self._step(tick)
            except Exception:  # noqa: BLE001 - a watcher must never die
                log.exception("proactive step failed")

    async def _step(self, tick):
        state = self.session.state.value
        w = foreground_window()
        if w and runtime.memory is not None:
            runtime.memory.track(w.spoken_name, w.title)

        # Schedule: always announced (like reminders), even in standby.
        if tick % 10 == 0:
            for event, mins in schedule.due_soon(datetime.datetime.now(), self.announced):
                await self.session.announce(f"Heads up: {event['title']} starts in {mins} minutes." if mins else
                                            f"{event['title']} is starting now.", kind="reminder")

        if not self.on or state in ("standby", "listening", "thinking", "speaking"):
            return

        # Error dialogs: offer help once per dialog.
        hwnd = user32.GetForegroundWindow()
        if hwnd and hwnd not in self.seen_dialogs and is_dialog(hwnd):
            self.seen_dialogs.add(hwnd)
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            body = dialog_text(hwnd)
            if ERROR_WORDS.search(buf.value + " " + body):
                log.info("error dialog: %r %r", buf.value, body[:120])
                if runtime.memory is not None:
                    runtime.memory.add("screen", f"Error dialog: {buf.value}", body)
                tools = self.session.agent.tools

                async def explain():
                    await tools.speak("Let me take a look.")
                    shot = await tools.call("look_at_screen", {"question": f"Explain this error and how to fix it: {buf.value} {body}"})
                    if isinstance(shot, dict):
                        await self.session.agent._vision_answer("explain the error", shot, {})

                tools.pending_confirm = explain
                await self.session.announce(f"Looks like {buf.value or 'a program'} just showed an error. Want me to explain it?")

        # Break suggestion after a long stretch of activity.
        idle = idle_seconds()
        now = time.time()
        if idle > IDLE_RESET_S:
            self.active_since = now
            self.break_suggested = False
        elif not self.break_suggested and now - self.active_since > BREAK_AFTER_S:
            self.break_suggested = True
            mins = int((now - self.active_since) // 60)
            await self.session.announce(f"You've been going for {mins} minutes straight. Maybe stand up and stretch for a bit?")
