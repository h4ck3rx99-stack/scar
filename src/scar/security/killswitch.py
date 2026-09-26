"""Emergency stop (C4.12).

A global hotkey (pynput, no admin rights) or ``trigger()`` immediately:
halts input simulation, cancels all running tasks, kills managed child process
trees and stops TTS. Input stays halted until the user explicitly starts a new
task.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import structlog

log = structlog.get_logger("scar.killswitch")


class InputGate:
    """Process-wide switch checked before every simulated input event."""

    def __init__(self) -> None:
        self._halted = threading.Event()
        self.reason = ""

    def halt(self, reason: str) -> None:
        self.reason = reason
        self._halted.set()

    def reset(self) -> None:
        self.reason = ""
        self._halted.clear()

    @property
    def halted(self) -> bool:
        return self._halted.is_set()

    def check(self) -> None:
        if self._halted.is_set():
            from scar.core.errors import Cancelled

            raise Cancelled(f"input halted: {self.reason}")


INPUT_GATE = InputGate()


class KillSwitch:
    def __init__(self, hotkey: str) -> None:
        self.hotkey = hotkey
        self._callbacks: list[tuple[str, Callable[[], None]]] = []
        self._listener: object | None = None
        self._lock = threading.Lock()
        self.triggered_at: float | None = None
        self.last_error: str | None = None

    def register(self, name: str, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append((name, callback))

    def trigger(self, source: str = "hotkey") -> list[str]:
        """Run every stop action; returns the names of actions that ran."""
        INPUT_GATE.halt(f"kill switch ({source})")
        self.triggered_at = time.time()
        ran: list[str] = []
        with self._lock:
            callbacks = list(self._callbacks)
        for name, cb in callbacks:
            try:
                cb()
                ran.append(name)
            except Exception as exc:  # noqa: BLE001 - every stop action must be attempted
                log.error("killswitch_callback_failed", callback=name, error=str(exc))
        log.warning("killswitch_triggered", source=source, actions=ran)
        return ran

    def start(self) -> bool:
        """Start the global hotkey listener. Returns False if unavailable."""
        try:
            from pynput import keyboard
        except ImportError as exc:  # pragma: no cover - dependency is declared
            self.last_error = str(exc)
            return False
        try:
            listener = keyboard.GlobalHotKeys({self.hotkey: lambda: self.trigger("hotkey")})
            listener.daemon = True
            listener.start()
        except (ValueError, OSError, RuntimeError) as exc:
            self.last_error = f"cannot register hotkey {self.hotkey!r}: {exc}"
            return False
        self._listener = listener
        return True

    def stop(self) -> None:
        listener = self._listener
        if listener is not None:
            stop = getattr(listener, "stop", None)
            if callable(stop):
                stop()
            self._listener = None

    @property
    def active(self) -> bool:
        return self._listener is not None
