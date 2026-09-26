"""On-demand screen capture (mss) in virtual-screen physical pixels."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from scar.core.errors import ToolError


@dataclass
class Capture:
    image: Image.Image
    left: int  # virtual-screen origin of the image
    top: int
    source: str  # "screen" | "monitor N" | "window HWND" | "region"
    window_title: str = ""

    def to_screen(self, x: float, y: float, scale: float = 1.0) -> tuple[int, int]:
        """Map image coords (possibly from a scaled copy) back to virtual-screen pixels."""
        return round(self.left + x / scale), round(self.top + y / scale)


def grab(left: int, top: int, width: int, height: int) -> Image.Image:
    import mss

    if width <= 0 or height <= 0:
        raise ToolError("empty capture region", "InvalidInput")
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_screen() -> Capture:
    from scar.tools.windows.win32 import virtual_screen

    vl, vt, vw, vh = virtual_screen()
    return Capture(grab(vl, vt, vw, vh), vl, vt, "screen")


def capture_monitor(index: int) -> Capture:
    from scar.tools.windows.win32 import list_monitors

    mons = list_monitors()
    if index < 0 or index >= len(mons):
        raise ToolError(f"monitor {index} does not exist (have {len(mons)})", "InvalidInput")
    m = mons[index]
    return Capture(grab(m.left, m.top, m.right - m.left, m.bottom - m.top), m.left, m.top, f"monitor {index}")


def capture_window(hwnd: int, bring_to_front: bool = True) -> Capture:
    from scar.tools.windows import win32

    if not win32.window_exists(hwnd):
        raise ToolError(f"window {hwnd} does not exist", "NotFound")
    info = win32.window_info(hwnd)
    if info.minimized or bring_to_front:
        win32.focus_window(hwnd)
        import time

        time.sleep(0.25)
        info = win32.window_info(hwnd)
    return Capture(grab(info.left, info.top, info.width, info.height), info.left, info.top, f"window {hwnd}", info.title)


def capture_region(left: int, top: int, width: int, height: int) -> Capture:
    return Capture(grab(left, top, width, height), left, top, "region")


def foreground_capture() -> Capture:
    from scar.tools.windows import win32

    hwnd = win32.foreground_hwnd()
    if hwnd and win32.window_exists(hwnd):
        info = win32.window_info(hwnd)
        if info.width > 50 and info.height > 50 and not info.minimized:
            return Capture(grab(info.left, info.top, info.width, info.height), info.left, info.top, f"window {hwnd}", info.title)
    return capture_screen()
