"""Full-screen / game / presentation awareness.

Windows reports the user's "quiet" states through SHQueryUserNotificationState (full-screen Direct3D app, presentation
mode, busy full-screen app, quiet time). Borderless-window games often report "accepts notifications", so the
foreground window covering its whole monitor also counts. While active (and ``game_mode = auto``), SCAR defers
toasts and speech and does not start live automation or local GPU inference unless the user explicitly asked.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

QUNS = {1: "not_present", 2: "busy", 3: "d3d_full_screen", 4: "presentation_mode", 5: "accepts_notifications",
        6: "quiet_time", 7: "app"}
_QUIET = {"busy", "d3d_full_screen", "presentation_mode", "quiet_time"}
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}
_CACHE_S = 2.0


@dataclass(frozen=True)
class FocusState:
    active: bool
    reason: str  # "d3d_full_screen", "presentation_mode", "full_screen_window", "" …
    process: str = ""


_last: tuple[float, FocusState] | None = None


def notification_state() -> str:
    if sys.platform != "win32":
        return "accepts_notifications"
    state = ctypes.c_int(0)
    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:  # type: ignore[attr-defined]
        return "unknown"
    return QUNS.get(state.value, "unknown")


def _fullscreen_foreground() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, ""
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False, ""
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    if cls.value in _SHELL_CLASSES:
        return False, ""
    # an ordinary maximized window (title bar, or WS_MAXIMIZE) is not full screen; borderless games/players have neither
    ws_caption, ws_maximize = 0x00C00000, 0x01000000
    style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
    if user32.IsZoomed(hwnd) or (style & ws_caption) == ws_caption or style & ws_maximize:
        return False, ""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False, ""

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False, ""
    m = mi.rcMonitor
    covers = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    if not covers:
        return False, ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        import psutil

        name = psutil.Process(pid.value).name()
    except Exception:  # noqa: BLE001 - process may have exited; the full-screen fact still stands
        name = ""
    return True, name


def focus_state(force: bool = False) -> FocusState:
    """Cached for 2 s: cheap enough to call before every toast, utterance or live action."""
    global _last
    now = time.monotonic()
    if not force and _last is not None and now - _last[0] < _CACHE_S:
        return _last[1]
    quns = notification_state()
    if quns in _QUIET:
        st = FocusState(True, quns)
    else:
        full, proc = _fullscreen_foreground()
        # browsers/video players in full screen count too: the user is watching something
        st = FocusState(full, "full_screen_window" if full else "", proc)
    _last = (now, st)
    return st


def game_mode_active(settings: object | None = None) -> bool:
    if settings is not None and getattr(settings, "game_mode", "auto") == "off":
        return False
    return focus_state().active
