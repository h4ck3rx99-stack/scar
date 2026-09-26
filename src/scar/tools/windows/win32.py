"""Win32 window management (C9.4): enumeration, focus, placement, state, close, DPI, elevation.

Coordinates are physical pixels in the virtual-screen space (monitors can have
negative origins). The process is made per-monitor-v2 DPI aware at startup so
window rects, UIA rects, screenshots and SendInput agree.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from typing import Any

import psutil

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
else:  # pragma: no cover - Windows-only module
    user32 = kernel32 = shcore = advapi32 = None  # type: ignore[assignment]

SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOW = 6, 3, 9, 5
WM_CLOSE = 0x0010
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
GW_OWNER = 4
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

_dpi_set = False


def set_dpi_awareness() -> str:
    """Per-monitor v2 DPI awareness (idempotent). Returns the mode achieved."""
    global _dpi_set
    if sys.platform != "win32":
        return "n/a"
    if _dpi_set:
        return "per-monitor-v2"
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            _dpi_set = True
            return "per-monitor-v2"
        err = ctypes.get_last_error()
        if err == 5:  # ERROR_ACCESS_DENIED: already set for the process
            _dpi_set = True
            return "already-set"
    except (AttributeError, OSError):
        pass
    try:
        shcore.SetProcessDpiAwareness(2)
        _dpi_set = True
        return "per-monitor"
    except (AttributeError, OSError):
        return "unaware"


def dpi_awareness() -> str:
    if sys.platform != "win32":
        return "n/a"
    try:
        ctx = user32.GetThreadDpiAwarenessContext()
        aw = user32.GetAwarenessFromDpiAwarenessContext(ctx)
        return {0: "unaware", 1: "system", 2: "per-monitor"}.get(aw, str(aw))
    except (AttributeError, OSError):
        return "unknown"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    class_name: str
    left: int
    top: int
    right: int
    bottom: int
    visible: bool
    minimized: bool
    maximized: bool
    foreground: bool
    hung: bool
    elevated: bool | None = None

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    # DWM extended frame bounds excludes the invisible resize border
    try:
        dwm = ctypes.WinDLL("dwmapi")
        if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            return r.left, r.top, r.right, r.bottom
    except OSError:
        pass
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def is_process_elevated(pid: int) -> bool | None:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            elevation = wintypes.DWORD()
            size = wintypes.DWORD()
            if not advapi32.GetTokenInformation(tok, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(size)):
                return None
            return bool(elevation.value)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def self_elevated() -> bool:
    import os

    return bool(is_process_elevated(os.getpid()))


def window_info(hwnd: int, fg: int | None = None) -> WindowInfo:
    pid = window_pid(hwnd)
    try:
        pname = psutil.Process(pid).name()
    except psutil.Error:
        pname = ""
    left, top, right, bottom = _rect(hwnd)
    placement = _placement(hwnd)
    return WindowInfo(
        hwnd=hwnd, title=_title(hwnd), pid=pid, process=pname, class_name=_class(hwnd), left=left, top=top,
        right=right, bottom=bottom, visible=bool(user32.IsWindowVisible(hwnd)), minimized=bool(user32.IsIconic(hwnd)),
        maximized=placement == SW_MAXIMIZE, foreground=hwnd == (fg if fg is not None else user32.GetForegroundWindow()),
        hung=bool(user32.IsHungAppWindow(hwnd)),
    )


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", wintypes.RECT)]


def _placement(hwnd: int) -> int:
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    return int(wp.showCmd)


def _is_app_window(hwnd: int) -> bool:
    if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
        return False
    if not _title(hwnd).strip():
        return False
    # skip cloaked (hidden UWP / other virtual desktop) windows
    try:
        dwm = ctypes.WinDLL("dwmapi")
        cloaked = wintypes.DWORD()
        if dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return False
    except OSError:
        pass
    ex = user32.GetWindowLongW(hwnd, -20)
    return not (ex & 0x00000080)  # WS_EX_TOOLWINDOW


def list_windows(include_all: bool = False) -> list[WindowInfo]:
    hwnds: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd: int, _lp: int) -> bool:
        if include_all or _is_app_window(hwnd):
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    fg = user32.GetForegroundWindow()
    return [window_info(h, fg) for h in hwnds]


def find_windows(title: str | None = None, process: str | None = None, pid: int | None = None) -> list[WindowInfo]:
    out = []
    for w in list_windows():
        if title and title.lower() not in w.title.lower():
            continue
        if process and process.lower().removesuffix(".exe") != w.process.lower().removesuffix(".exe"):
            continue
        if pid is not None and w.pid != pid:
            continue
        out.append(w)
    return out


def foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


def focus_window(hwnd: int) -> bool:
    """Bring a window to the foreground using the documented legitimate techniques."""
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() == hwnd:
        return True
    user32.AllowSetForegroundWindow(-1)
    if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
        return True
    # foreground lock: a synthetic ALT tap gives this process the last input event, which permits the switch
    from scar.tools.input.sendinput import tap_alt

    tap_alt()
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # attach input queues to the current foreground thread as a last resort
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    my_thread = kernel32.GetCurrentThreadId()
    if fg_thread and fg_thread != my_thread:
        user32.AttachThreadInput(my_thread, fg_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(my_thread, fg_thread, False)
    time.sleep(0.05)
    return bool(user32.GetForegroundWindow() == hwnd)


def set_state(hwnd: int, state: str) -> None:
    cmd = {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": SW_RESTORE, "show": SW_SHOW}[state]
    user32.ShowWindow(hwnd, cmd)


def move_resize(hwnd: int, left: int | None, top: int | None, width: int | None, height: int | None) -> None:
    if user32.IsIconic(hwnd) or _placement(hwnd) == SW_MAXIMIZE:
        user32.ShowWindow(hwnd, SW_RESTORE)
    l, t, r, b = _rect(hwnd)
    wl = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wl))
    # compensate for the invisible DWM border so the *visible* frame lands where asked
    dx_l, dy_t = l - wl.left, t - wl.top
    dx_r, dy_b = wl.right - r, wl.bottom - b
    x = (left if left is not None else l) - dx_l
    y = (top if top is not None else t) - dy_t
    w = (width if width is not None else r - l) + dx_l + dx_r
    h = (height if height is not None else b - t) + dy_t + dy_b
    user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def window_exists(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


@dataclass
class MonitorInfo:
    index: int
    left: int
    top: int
    right: int
    bottom: int
    primary: bool
    dpi: int
    scale: float
    name: str


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def list_monitors() -> list[MonitorInfo]:
    mons: list[MonitorInfo] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon: int, _hdc: int, _rect: Any, _lp: int) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        dx, dy = wintypes.UINT(), wintypes.UINT()
        try:
            shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy))
            dpi = int(dx.value) or 96
        except OSError:
            dpi = 96
        r = info.rcMonitor
        mons.append(MonitorInfo(len(mons), r.left, r.top, r.right, r.bottom, bool(info.dwFlags & 1), dpi, dpi / 96.0,
                                info.szDevice))
        return True

    user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    return mons


def virtual_screen() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole virtual desktop."""
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77), user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def wait_for_window(predicate: Any, timeout_s: float, interval: float = 0.4) -> WindowInfo | None:
    """Bounded wait for a window matching predicate(WindowInfo) -> bool."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for w in list_windows():
            if predicate(w):
                return w
        time.sleep(interval)
    return None
