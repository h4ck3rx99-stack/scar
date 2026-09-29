"""UI Automation lookups (caption buttons, browser tabs). UIA is COM/apartment-bound and can be slow,
so every call runs on one dedicated worker thread with a hard timeout."""
import asyncio
import concurrent.futures
import ctypes
import logging
from ctypes import wintypes

from jarvis.hands.windows import Rect, Window, dwmapi

log = logging.getLogger("uia")

_auto = None
_uia_init = None


def _init_thread():
    global _auto, _uia_init
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(0.3)
    _auto = auto
    _uia_init = auto.UIAutomationInitializerInThread(debug=False)


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia", initializer=_init_thread)


async def run(fn, *args, timeout=1.5):
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(_pool, fn, *args), timeout)
    except Exception as e:  # noqa: BLE001 - UIA failures always fall back to geometry
        log.debug("uia %s failed: %s", fn.__name__, e)
        return None


_BUTTON_NAMES = {
    "minimize": ("Minimize", "Minimise"),
    "maximize": ("Maximize", "Maximise", "Restore", "Restore Down"),
    "restore": ("Restore", "Restore Down", "Maximize", "Maximise"),
    "close": ("Close",),
}


def _find_button(hwnd, action):
    names = _BUTTON_NAMES[action]
    root = _auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    found = []

    def visit(ctrl, depth):
        if depth > 5 or found:
            return
        for child in ctrl.GetChildren():
            if child.ControlTypeName == "ButtonControl" and child.Name in names:
                r = child.BoundingRectangle
                if r.width() > 0 and r.height() > 0:
                    found.append(Rect(r.left, r.top, r.right, r.bottom))
                    return
            if child.ControlTypeName in ("TitleBarControl", "PaneControl", "GroupControl", "WindowControl", "CustomControl", "ToolBarControl"):
                visit(child, depth + 1)

    visit(root, 0)
    return found[0] if found else None


DWMWA_CAPTION_BUTTON_BOUNDS = 5


def _dwm_caption_bounds(w: Window):
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(w.hwnd, DWMWA_CAPTION_BUTTON_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        return None
    if r.right - r.left <= 0:
        return None
    wr = w.rect  # caption bounds are relative to the window rect
    return Rect(wr.left + r.left, wr.top + r.top, wr.left + r.right, wr.top + r.bottom)


async def caption_button_point(w: Window, action: str):
    """Screen point of a window's minimize/maximize/close button: UIA first, then DWM, then geometry."""
    rect = await run(_find_button, w.hwnd, action)
    if rect:
        return (rect.left + rect.width // 2, rect.top + rect.height // 2), "uia"
    idx = {"close": 0, "maximize": 1, "restore": 1, "minimize": 2}[action]
    cap = _dwm_caption_bounds(w)
    if cap and cap.width > 60:
        bw = cap.width / 3
        return (int(cap.right - bw * idx - bw / 2), cap.top + cap.height // 2), "dwm"
    s, f = w.dpi_scale, w.frame
    return (int(f.right - (23 + 46 * idx) * s), int(f.top + 16 * s)), "geometry"


# ----------------------------------------------------------------- browser tabs
def _tabs(hwnd):
    root = _auto.ControlFromHandle(hwnd)
    tabs = []

    def visit(ctrl, depth):
        if depth > 12:
            return
        for child in ctrl.GetChildren():
            if child.ControlTypeName == "TabItemControl":
                tabs.append(child)
            elif child.ControlTypeName not in ("DocumentControl",):  # never descend into page content
                visit(child, depth + 1)

    visit(root, 0)
    return tabs


def _list_tabs(hwnd):
    out = []
    for t in _tabs(hwnd):
        r = t.BoundingRectangle
        out.append({"name": t.Name, "center": ((r.left + r.right) // 2, (r.top + r.bottom) // 2)})
    return out


def _select_tab(hwnd, name_query):
    from rapidfuzz import fuzz
    best, score = None, 0
    for t in _tabs(hwnd):
        s = fuzz.partial_ratio(name_query.lower(), t.Name.lower())
        if s > score:
            best, score = t, s
    if best is None or score < 70:
        return None
    r = best.BoundingRectangle
    center = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    try:
        best.GetSelectionItemPattern().Select()
    except Exception:  # noqa: BLE001 - some browsers only support Invoke/click
        best.Click(simulateMove=False)
    return {"name": best.Name, "center": center}


async def list_tabs(w: Window):
    return await run(_list_tabs, w.hwnd, timeout=3) or []


async def select_tab(w: Window, query: str):
    return await run(_select_tab, w.hwnd, query, timeout=3)
