"""Keyboard and mouse input via SendInput (C9.5).

Every event batch checks the kill-switch gate first, and callers verify the
expected window is in the foreground before (and during) typing, so SCAR never
types into the wrong window. Mouse coordinates are virtual-desktop physical
pixels (multi-monitor, negative origins supported).
"""

from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable
from ctypes import wintypes

from scar.core.errors import FocusLost, ToolInputError
from scar.security.killswitch import INPUT_GATE

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetForegroundWindow.restype = wintypes.HWND
else:  # pragma: no cover
    _user32 = None

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "shift": 0x10, "ctrl": 0x11, "control": 0x11,
    "alt": 0x12, "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "pageup": 0x21,
    "pagedown": 0x22, "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "apps": 0x5D, "menu": 0x5D, "numlock": 0x90, "scrolllock": 0x91, "volume_mute": 0xAD, "volume_down": 0xAE,
    "volume_up": 0xAF, "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x2C}


def _send(inputs: list[INPUT]) -> None:
    INPUT_GATE.check()
    if _user32 is None:
        raise RuntimeError("SendInput is only available on Windows")
    arr = (INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        err = ctypes.get_last_error()
        raise OSError(f"SendInput delivered {sent}/{len(inputs)} events (error {err}); the target may be elevated (UIPI)")


def _key(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0))


def _unicode(ch: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=ch, dwFlags=flags, time=0, dwExtraInfo=0))


def tap_alt() -> None:
    _send([_key(0x12, False), _key(0x12, True)])


def press_vk(vk: int, times: int = 1) -> None:
    for _ in range(times):
        _send([_key(vk, False), _key(vk, True)])
        time.sleep(0.01)


def parse_hotkey(combo: str) -> list[int]:
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()] if combo != "+" else ["="]
    vks: list[int] = []
    for p in parts:
        if p not in VK:
            raise ToolInputError(f"unknown key {p!r} in {combo!r}")
        vks.append(VK[p])
    return vks


def hotkey(combo: str, guard: Callable[[], None] | None = None) -> None:
    vks = parse_hotkey(combo)
    if guard:
        guard()
    events = [_key(v, False) for v in vks] + [_key(v, True) for v in reversed(vks)]
    _send(events)


def type_text(text: str, guard: Callable[[], None] | None = None, chunk: int = 24, delay: float = 0.012) -> int:
    """Type Unicode text; ``guard`` runs before every chunk (focus check). Returns characters typed."""
    typed = 0
    units = text.replace("\r\n", "\n")
    for i in range(0, len(units), chunk):
        if guard:
            guard()
        events: list[INPUT] = []
        for ch in units[i : i + chunk]:
            if ch == "\n":
                events += [_key(0x0D, False), _key(0x0D, True)]
                continue
            if ch == "\t":
                events += [_key(0x09, False), _key(0x09, True)]
                continue
            data = ch.encode("utf-16-le")
            for j in range(0, len(data), 2):
                code = int.from_bytes(data[j : j + 2], "little")
                events += [_unicode(code, False), _unicode(code, True)]
        _send(events)
        typed += len(units[i : i + chunk])
        time.sleep(delay)
    return typed


def _abs(x: int, y: int) -> tuple[int, int]:
    from scar.tools.windows.win32 import virtual_screen

    vl, vt, vw, vh = virtual_screen()
    nx = round((x - vl) * 65535 / max(1, vw - 1))
    ny = round((y - vt) * 65535 / max(1, vh - 1))
    return nx, ny


def _mouse(flags: int, x: int = 0, y: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=x, dy=y, mouseData=data & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0))


def move(x: int, y: int) -> None:
    nx, ny = _abs(x, y)
    _send([_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)])


def click(x: int, y: int, button: str = "left", count: int = 1, guard: Callable[[], None] | None = None) -> None:
    down, up = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP), "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}[button]
    move(x, y)
    time.sleep(0.03)
    if guard:
        guard()
    for _ in range(count):
        _send([_mouse(down), _mouse(up)])
        time.sleep(0.05)


def drag(x1: int, y1: int, x2: int, y2: int, steps: int = 20, guard: Callable[[], None] | None = None) -> None:
    move(x1, y1)
    if guard:
        guard()
    _send([_mouse(MOUSEEVENTF_LEFTDOWN)])
    try:
        for i in range(1, steps + 1):
            move(round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps))
            time.sleep(0.01)
    finally:
        _send([_mouse(MOUSEEVENTF_LEFTUP)])


def scroll(x: int, y: int, clicks: int, horizontal: bool = False) -> None:
    move(x, y)
    _send([_mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, data=int(clicks * 120))])


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    if _user32 is not None:
        _user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def focus_guard(hwnd: int) -> Callable[[], None]:
    """Return a callable that raises FocusLost unless ``hwnd`` (or its owned popup) is the foreground window."""

    def check() -> None:
        INPUT_GATE.check()
        if _user32 is None:
            return
        fg = int(_user32.GetForegroundWindow() or 0)
        if fg == hwnd:
            return
        # accept a popup/dialog owned by the target (e.g. an autocomplete list)
        _user32.GetWindow.restype = wintypes.HWND
        _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        owner = int(_user32.GetWindow(fg, 4) or 0)
        if owner == hwnd:
            return
        from scar.tools.windows.win32 import window_pid

        if fg and window_pid(fg) == window_pid(hwnd) and owner:
            return
        raise FocusLost(f"focus moved away from the target window (foreground is now {fg}); input aborted")

    return check
