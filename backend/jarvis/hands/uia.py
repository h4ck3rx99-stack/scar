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


async def caption_button(w: Window, action: str):
    """Screen rect of a window's minimize/maximize/close button: UIA first, then DWM, then geometry.
    Returns (rect, how)."""
    rect = await run(_find_button, w.hwnd, action)
    if rect:
        return rect, "uia"
    idx = {"close": 0, "maximize": 1, "restore": 1, "minimize": 2}[action]
    cap = _dwm_caption_bounds(w)
    if cap and cap.width > 60:
        bw = cap.width / 3
        right = cap.right - bw * idx
        return Rect(int(right - bw), cap.top, int(right), cap.bottom), "dwm"
    s, f = w.dpi_scale, w.frame
    right = f.right - 46 * idx * s
    return Rect(int(right - 46 * s), f.top, int(right), int(f.top + 32 * s)), "geometry"


async def caption_button_point(w: Window, action: str):
    rect, how = await caption_button(w, action)
    return (rect.left + rect.width // 2, rect.top + rect.height // 2), how


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


# ------------------------------------------------------------ click by name
_CLICKABLE = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl", "ListItemControl",
              "CheckBoxControl", "RadioButtonControl", "SplitButtonControl", "TreeItemControl", "ComboBoxControl",
              "EditControl", "DataItemControl", "TextControl", "ImageControl"}
_PREFERRED = {"ButtonControl": 12, "HyperlinkControl": 10, "MenuItemControl": 10, "TabItemControl": 8,
              "CheckBoxControl": 8, "RadioButtonControl": 8, "SplitButtonControl": 8, "ListItemControl": 6,
              "TreeItemControl": 6, "EditControl": 5, "ComboBoxControl": 5, "DataItemControl": 4, "TextControl": 0, "ImageControl": 0}


def _find_element(hwnd, query, max_nodes=4000, budget_s=2.5):
    """Breadth-first search of the window's accessibility tree for the best on-screen element named like `query`."""
    import time
    from rapidfuzz import fuzz
    q = query.lower().strip()
    root = _auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    wr = root.BoundingRectangle
    queue, seen, best, best_score = [root], 0, None, 0
    t0 = time.monotonic()
    while queue and seen < max_nodes and time.monotonic() - t0 < budget_s:
        ctrl = queue.pop(0)
        seen += 1
        try:
            children = ctrl.GetChildren()
        except Exception:  # noqa: BLE001 - elements vanish while we walk
            continue
        for c in children:
            queue.append(c)
            try:
                kind, name = c.ControlTypeName, (c.Name or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if kind not in _CLICKABLE or not name or len(name) > 120:
                continue
            n = name.lower()
            score = 100 if n == q else fuzz.ratio(q, n) if len(n) < len(q) * 2.5 else fuzz.partial_ratio(q, n) - 12
            score += _PREFERRED.get(kind, 0)
            if score > best_score:
                r = c.BoundingRectangle
                # Must be visible inside the window.
                if r.width() > 2 and r.height() > 2 and r.left >= wr.left - 5 and r.top >= wr.top - 5 and r.right <= wr.right + 5 and r.bottom <= wr.bottom + 5:
                    best, best_score = c, score
    if best is None or best_score < 78:
        return None
    r = best.BoundingRectangle
    return best, Rect(r.left, r.top, r.right, r.bottom)


def _invoke(ctrl):
    for getter in ("GetInvokePattern", "GetSelectionItemPattern", "GetTogglePattern", "GetExpandCollapsePattern"):
        try:
            pattern = getattr(ctrl, getter)()
            if pattern is None:
                continue
            if getter == "GetInvokePattern":
                pattern.Invoke()
            elif getter == "GetSelectionItemPattern":
                pattern.Select()
            elif getter == "GetTogglePattern":
                pattern.Toggle()
            else:
                pattern.Expand()
            return True
        except Exception:  # noqa: BLE001 - try the next way of pressing it
            continue
    ctrl.Click(simulateMove=False)
    return True


async def find_element(w: Window, query: str):
    """Returns (control, rect) or None. Chromium browsers build their accessibility tree on first request,
    so an empty first search is retried once."""
    hit = await run(_find_element, w.hwnd, query, timeout=4)
    if hit is None:
        await asyncio.sleep(0.4)
        hit = await run(_find_element, w.hwnd, query, timeout=4)
    return hit


async def invoke(ctrl):
    return await run(_invoke, ctrl, timeout=3)


def _find_search_box(hwnd, max_nodes=3000, budget_s=2.0):
    """The window's search field: a focused edit box, else an edit/combo box named or hinted like 'search'."""
    import time
    root = _auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    try:
        foc = _auto.GetFocusedControl()
        if foc and foc.ControlTypeName in ("EditControl", "ComboBoxControl") and "search" in (
                (foc.Name or "") + (foc.AutomationId or "") + (foc.HelpText or "")).lower():
            return foc
    except Exception:  # noqa: BLE001
        pass
    queue, seen, fallback = [root], 0, None
    t0 = time.monotonic()
    while queue and seen < max_nodes and time.monotonic() - t0 < budget_s:
        ctrl = queue.pop(0)
        seen += 1
        try:
            children = ctrl.GetChildren()
        except Exception:  # noqa: BLE001
            continue
        for c in children:
            queue.append(c)
            try:
                kind = c.ControlTypeName
                if kind not in ("EditControl", "ComboBoxControl"):
                    continue
                hint = ((c.Name or "") + " " + (c.AutomationId or "") + " " + (c.HelpText or "")).lower()
                r = c.BoundingRectangle
                if r.width() < 20:
                    continue
                if "search" in hint or "what do you want" in hint or "find" in hint:
                    return c
                if fallback is None and "address" not in hint and "url" not in hint:
                    fallback = c
            except Exception:  # noqa: BLE001
                continue
    return fallback


def _type_into(ctrl, text):
    """Focus the field, replace its text, press Enter."""
    ctrl.SetFocus()
    try:
        vp = ctrl.GetValuePattern()
        vp.SetValue(text)
    except Exception:  # noqa: BLE001 - not every field supports ValuePattern: type it instead
        ctrl.Click(simulateMove=False)
        _auto.SendKeys("{Ctrl}a", waitTime=0.05)
        _auto.SendKeys(text.replace("{", "{{}").replace("}", "{}}"), interval=0.005, waitTime=0.05)
    _auto.SendKeys("{Enter}", waitTime=0.05)
    r = ctrl.BoundingRectangle
    return Rect(r.left, r.top, r.right, r.bottom)


async def search_in(w: Window, text: str):
    """Type a query into the window's own search bar. Returns the box's rect, or None if there is none."""
    box = await run(_find_search_box, w.hwnd, timeout=4)
    if box is None:
        return None
    return await run(_type_into, box, text, timeout=4)
