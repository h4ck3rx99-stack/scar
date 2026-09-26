"""Notifications (C9.12): Windows toast + TTS (when a voice session is active) + CLI event."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import structlog

from scar.core.events import AssistantMessage

log = structlog.get_logger("scar.notify")


@dataclass
class Delivery:
    at: float
    title: str
    message: str
    channels: list[str]
    task_id: str | None


class Notifier:
    def __init__(self, services: Any) -> None:
        self.s = services
        self.delivered: deque[Delivery] = deque(maxlen=200)
        self._toaster: Any = None
        self.toast_error: str | None = None

    def _toast(self, title: str, message: str) -> bool:
        if sys.platform != "win32":
            return False
        try:
            from windows_toasts import Toast, WindowsToaster

            if self._toaster is None:
                self._toaster = WindowsToaster("SCAR")
            toast = Toast([title, message[:250]])
            toast.on_activated = lambda _args: _focus_console()
            self._toaster.show_toast(toast)
            return True
        except Exception as exc:  # noqa: BLE001 - WinRT notification errors are environment-specific
            self.toast_error = str(exc)[:200]
            log.warning("toast_failed", error=self.toast_error)
            return False

    async def notify(self, title: str, message: str, *, task_id: str | None = None, speak: bool = True,
                     toast: bool = True) -> Delivery:
        channels: list[str] = []
        if toast and await asyncio.to_thread(self._toast, title, message):
            channels.append("toast")
        voice = self.s.voice
        if speak and voice is not None and getattr(voice, "active", False):
            with contextlib.suppress(Exception):
                await voice.speak(message)
                channels.append("tts")
        self.s.bus.publish(AssistantMessage(task_id=task_id, message=f"{title}: {message}", final=True))
        channels.append("cli")
        d = Delivery(time.time(), title, message, channels, task_id)
        self.delivered.append(d)
        log.info("notification", title=title, channels=channels)
        return d


def _focus_console() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
