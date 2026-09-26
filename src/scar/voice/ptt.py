"""Push-to-talk global hotkey (pynput; no admin rights)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PushToTalk:
    def __init__(self, hotkey: str, on_press: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self._listener: Any = None
        self.error: str | None = None

    def start(self) -> bool:
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self.hotkey: self.on_press})
            self._listener.daemon = True
            self._listener.start()
            return True
        except (ImportError, ValueError, OSError, RuntimeError) as exc:
            self.error = f"cannot register push-to-talk hotkey {self.hotkey!r}: {exc}"
            return False

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
