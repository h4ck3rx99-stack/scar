"""Window snapshot + temporary invisibility, used for the cloth/rubber proxy card.

Safety rule: a window hidden here is ALWAYS made visible again: by `reveal`, by the watchdog, or by
`reveal_all` at shutdown. Leaving a user's window invisible is the worst bug this app could have.
"""
import ctypes
import logging
import threading
import time
import uuid

import win32gui
import win32ui
from PIL import Image

from jarvis import config
from jarvis.hands.windows import Window, user32

log = logging.getLogger("capture")

PW_RENDERFULLCONTENT = 2
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x80000
LWA_ALPHA = 2
WATCHDOG_S = 4.0

_hidden = {}  # hwnd -> (original exstyle, deadline)
_lock = threading.Lock()


def snapshot(w: Window) -> tuple[str, dict]:
    """Capture the window's visible frame to captures/<id>.webp. Returns (filename, bounds)."""
    r, f = w.rect, w.frame
    width, height = r.width, r.height
    hwnd_dc = win32gui.GetWindowDC(w.hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, width, height)
    mem_dc.SelectObject(bmp)
    try:
        ctypes.windll.user32.PrintWindow(w.hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(w.hwnd, hwnd_dc)
    img = img.crop((f.left - r.left, f.top - r.top, f.right - r.left, f.bottom - r.top))
    # Big windows are downscaled: the card is in motion and stretched, 1280 px wide is plenty.
    if img.width > 1280:
        img = img.resize((1280, round(img.height * 1280 / img.width)), Image.LANCZOS)
    config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    for old in sorted(config.CAPTURE_DIR.glob("*.webp"))[:-8]:
        old.unlink(missing_ok=True)
    name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.webp"
    img.save(config.CAPTURE_DIR / name, "WEBP", quality=82, method=2)
    return name, f.as_dict()


def hide(w: Window):
    style = user32.GetWindowLongW(w.hwnd, GWL_EXSTYLE)
    with _lock:
        if w.hwnd not in _hidden:
            _hidden[w.hwnd] = [style, time.monotonic() + WATCHDOG_S]
    user32.SetWindowLongW(w.hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    user32.SetLayeredWindowAttributes(w.hwnd, 0, 0, LWA_ALPHA)
    _ensure_watchdog()


def extend(w: Window, seconds=WATCHDOG_S):
    with _lock:
        if w.hwnd in _hidden:
            _hidden[w.hwnd][1] = time.monotonic() + seconds


def reveal(hwnd: int):
    with _lock:
        entry = _hidden.pop(hwnd, None)
    if entry is None:
        return
    style = entry[0]
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    if not style & WS_EX_LAYERED:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)  # INVALIDATE|ERASE|ALLCHILDREN|FRAME


def reveal_all():
    for hwnd in list(_hidden):
        reveal(hwnd)


_watchdog = None


def _ensure_watchdog():
    global _watchdog
    if _watchdog and _watchdog.is_alive():
        return

    def loop():
        while True:
            time.sleep(0.25)
            now = time.monotonic()
            with _lock:
                expired = [h for h, (_, dl) in _hidden.items() if dl < now]
                empty = not _hidden
            for h in expired:
                log.warning("watchdog revealing window %s", hex(h))
                reveal(h)
            if empty:
                return

    _watchdog = threading.Thread(target=loop, daemon=True, name="reveal-watchdog")
    _watchdog.start()
