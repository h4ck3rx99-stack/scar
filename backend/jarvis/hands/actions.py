"""Window and system actions. Actions go through direct Win32 calls, so they cannot miss; the avatar's
flight and 'tap' are purely visual unless JARVIS_REAL_CLICK is on."""
import ctypes
import os
import re
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path

from rapidfuzz import fuzz, process

from jarvis.hands.windows import Rect, Window, user32

SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE, SW_SHOW = 3, 6, 9, 5
WM_SYSCOMMAND, SC_CLOSE = 0x0112, 0xF060
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITOR_DEFAULTTONEAREST = 2
KEYEVENTF_KEYUP = 0x2
VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 0x09, "w": 0x57, "t": 0x54, "f4": 0x73,
      "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_info(hmon):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcWork
    return Rect(r.left, r.top, r.right, r.bottom)


def work_area(w: Window) -> Rect:
    return _monitor_info(user32.MonitorFromWindow(w.hwnd, MONITOR_DEFAULTTONEAREST))


def all_work_areas():
    areas = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def cb(hmon, hdc, rect, lp):
        areas.append(_monitor_info(hmon))
        return True

    user32.EnumDisplayMonitors(0, 0, cb, 0)
    return sorted(areas, key=lambda r: (r.left, r.top))


def press(*keys):
    for k in keys:
        user32.keybd_event(VK[k], 0, 0, 0)
    for k in reversed(keys):
        user32.keybd_event(VK[k], 0, KEYEVENTF_KEYUP, 0)


def focus(w: Window):
    if w.minimized:
        user32.ShowWindow(w.hwnd, SW_RESTORE)
    # Windows only lets the foreground process change focus; a synthetic Alt tap lifts that lock.
    press("alt")
    user32.SetForegroundWindow(w.hwnd)


def minimize(w):
    user32.ShowWindow(w.hwnd, SW_MINIMIZE)


def maximize(w):
    user32.ShowWindow(w.hwnd, SW_MAXIMIZE)


def restore(w):
    user32.ShowWindow(w.hwnd, SW_RESTORE)


def close(w):
    user32.PostMessageW(w.hwnd, WM_SYSCOMMAND, SC_CLOSE, 0)


def looks_unsaved(w: Window) -> bool:
    t = w.title
    return t.startswith("*") or t.startswith("●") or " ● " in t or "unsaved" in t.lower()


def set_frame(w: Window, target: Rect):
    """Place the window so its *visible* frame lands exactly on target (compensates invisible borders)."""
    if w.maximized or w.minimized:
        user32.ShowWindow(w.hwnd, SW_RESTORE)
        time.sleep(0.05)
    r, f = w.rect, w.frame
    dl, dt, dr, db = f.left - r.left, f.top - r.top, r.right - f.right, r.bottom - f.bottom
    user32.SetWindowPos(w.hwnd, 0, target.left - dl, target.top - dt,
                        target.width + dl + dr, target.height + dt + db, SWP_NOZORDER | SWP_NOACTIVATE)


LAYOUTS = {
    "left": (0, 0, .5, 1), "right": (.5, 0, 1, 1), "top": (0, 0, 1, .5), "bottom": (0, .5, 1, 1),
    "top_left": (0, 0, .5, .5), "top_right": (.5, 0, 1, .5), "bottom_left": (0, .5, .5, 1), "bottom_right": (.5, .5, 1, 1),
    "left_third": (0, 0, 1 / 3, 1), "center_third": (1 / 3, 0, 2 / 3, 1), "right_third": (2 / 3, 0, 1, 1),
    "left_two_thirds": (0, 0, 2 / 3, 1), "right_two_thirds": (1 / 3, 0, 1, 1),
    "center": (.15, .1, .85, .9),
}


def layout_rect(w: Window, layout: str) -> Rect:
    a = work_area(w)
    x0, y0, x1, y1 = LAYOUTS[layout]
    return Rect(round(a.left + a.width * x0), round(a.top + a.height * y0), round(a.left + a.width * x1), round(a.top + a.height * y1))


def aspect_rect(w: Window, ratio_w: float, ratio_h: float, fill=0.8) -> Rect:
    """Largest rect of the given aspect ratio covering `fill` of the work area, centred on the window."""
    a, f = work_area(w), w.frame
    maxw, maxh = a.width * fill, a.height * fill
    width = min(maxw, maxh * ratio_w / ratio_h)
    height = width * ratio_h / ratio_w
    cx, cy = (f.left + f.right) / 2, (f.top + f.bottom) / 2
    left = min(max(a.left, cx - width / 2), a.right - width)
    top = min(max(a.top, cy - height / 2), a.bottom - height)
    return Rect(round(left), round(top), round(left + width), round(top + height))


def scaled_rect(w: Window, factor: float) -> Rect:
    a, f = work_area(w), w.frame
    width = min(a.width, max(400, f.width * factor))
    height = min(a.height, max(300, f.height * factor))
    cx, cy = (f.left + f.right) / 2, (f.top + f.bottom) / 2
    left = min(max(a.left, cx - width / 2), a.right - width)
    top = min(max(a.top, cy - height / 2), a.bottom - height)
    return Rect(round(left), round(top), round(left + width), round(top + height))


def next_monitor_rect(w: Window):
    areas = all_work_areas()
    if len(areas) < 2:
        return None
    cur = work_area(w)
    i = next((k for k, a in enumerate(areas) if (a.left, a.top) == (cur.left, cur.top)), 0)
    dst = areas[(i + 1) % len(areas)]
    f = w.frame
    rx, ry = (f.left - cur.left) / cur.width, (f.top - cur.top) / cur.height
    width, height = min(f.width, dst.width), min(f.height, dst.height)
    left, top = dst.left + rx * dst.width, dst.top + ry * dst.height
    return Rect(round(left), round(top), round(left + width), round(top + height))


def real_click(x, y, restore=True):
    """A genuine mouse click at (x, y); the pointer goes back where it was, so the user barely notices."""
    old = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(old))
    user32.SetCursorPos(int(x), int(y))
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    if restore:
        time.sleep(0.05)
        user32.SetCursorPos(old.x, old.y)


# ------------------------------------------------------------------ open things
SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "github": "https://github.com", "netflix": "https://www.netflix.com", "whatsapp web": "https://web.whatsapp.com",
    "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "wikipedia": "https://www.wikipedia.org",
    "amazon": "https://www.amazon.in", "instagram": "https://www.instagram.com", "linkedin": "https://www.linkedin.com",
    "twitter": "https://x.com", "x": "https://x.com", "reddit": "https://www.reddit.com", "maps": "https://maps.google.com",
    "stack overflow": "https://stackoverflow.com", "spotify web": "https://open.spotify.com",
    "nxtwave": "https://learning.ccbp.in", "next wave": "https://learning.ccbp.in", "ccbp": "https://learning.ccbp.in",
}
SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={}", "google": "https://www.google.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}", "amazon": "https://www.amazon.in/s?k={}",
    "maps": "https://www.google.com/maps/search/{}", "github": "https://github.com/search?q={}",
}
APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe", "file explorer": "explorer.exe",
    "explorer": "explorer.exe", "files": "explorer.exe", "task manager": "taskmgr.exe", "command prompt": "cmd.exe",
    "terminal": "wt.exe", "powershell": "powershell.exe", "settings": "ms-settings:", "spotify": "spotify:",
    "camera": "microsoft.windows.camera:", "store": "ms-windows-store:", "snipping tool": "ms-screenclip:",
}
_START_DIRS = [Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
               Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]


def _start_menu_shortcuts():
    items = {}
    for d in _START_DIRS:
        if d.is_dir():
            for lnk in d.rglob("*.lnk"):
                name = lnk.stem.lower()
                if "uninstall" not in name and "readme" not in name:
                    items.setdefault(name, lnk)
    return items


_apps_cache = {"at": 0.0, "apps": {}}


def _start_apps():
    """Every app in the Start menu including Microsoft Store apps (WhatsApp, Spotify from the Store, Netflix...),
    as {lower name: AppUserModelID}. Cached for 10 minutes; the PowerShell call takes ~1 s."""
    import json
    import subprocess
    if time.time() - _apps_cache["at"] < 600 and _apps_cache["apps"]:
        return _apps_cache["apps"]
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        data = json.loads(out or "[]")
        data = [data] if isinstance(data, dict) else data
        apps = {d["Name"].lower(): d["AppID"] for d in data if d.get("Name") and d.get("AppID")
                and "uninstall" not in d["Name"].lower()}
        _apps_cache.update(at=time.time(), apps=apps)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return _apps_cache["apps"]


def open_url(url: str):
    if not re.match(r"^https?://", url):
        url = "https://" + url
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("only web links can be opened")
    os.startfile(url)
    return url


def site_url(name: str):
    return SITES.get(name.lower().strip())


def search_url(engine: str, query: str):
    tpl = SEARCH.get(engine.lower().strip(), SEARCH["google"])
    return tpl.format(urllib.parse.quote_plus(query))


def open_app(name: str):
    """Launch an app by spoken name. Returns the resolved name, or None if nothing matched."""
    key = name.lower().strip()
    if key in APPS:
        os.startfile(APPS[key])
        return key
    shortcuts = _start_menu_shortcuts()
    match = process.extractOne(key, shortcuts.keys(), scorer=fuzz.WRatio, score_cutoff=85)
    if match:
        os.startfile(str(shortcuts[match[0]]))
        return match[0]
    apps = _start_apps()
    match = process.extractOne(key, apps.keys(), scorer=fuzz.WRatio, score_cutoff=86)
    if match:
        import subprocess
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + apps[match[0]]])
        return match[0]
    if key in SITES:
        open_url(SITES[key])
        return key
    return None


def cycle_to_tab(w: Window, query: str, max_tabs=30):
    """Browser-agnostic tab switch: Ctrl+Tab through tabs until the window title matches."""
    focus(w)
    time.sleep(0.15)
    q = query.lower()
    start = w.title
    for _ in range(max_tabs):
        if fuzz.partial_ratio(q, w.title.lower()) >= 80:
            return w.title
        press("ctrl", "tab")
        time.sleep(0.12)
        if w.title == start:
            break
    return None


def close_tab(w: Window):
    focus(w)
    time.sleep(0.1)
    press("ctrl", "w")


def new_tab(w: Window):
    focus(w)
    time.sleep(0.1)
    press("ctrl", "t")


def media(key: str, times=1):
    for _ in range(times):
        press(key)
