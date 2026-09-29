"""Reading the screen (Windows' built-in OCR engine, fully offline), clipboard, and typing text."""
import asyncio
import ctypes
import time
from ctypes import wintypes

from PIL import ImageGrab

from jarvis.hands.windows import Window

user32 = ctypes.windll.user32


async def ocr_image(img) -> str:
    """OCR a PIL image with Windows.Media.Ocr (ships with Windows, no download, no network)."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    img = img.convert("RGBA")
    # The engine reads small text badly and huge images slowly; ~2400 px wide is the sweet spot.
    if img.width > 2600:
        img = img.resize((2600, round(img.height * 2600 / img.width)))
    elif img.width < 1200:
        img = img.resize((img.width * 2, img.height * 2))
    r, g, b, a = img.split()
    from PIL import Image
    bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(bgra)
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height)
    engine = OcrEngine.try_create_from_user_profile_languages()
    result = await engine.recognize_async(bmp)
    return "\n".join(line.text for line in result.lines)


async def read_window(w: Window | None) -> str:
    if w is not None and not w.minimized:
        f = w.frame
        img = ImageGrab.grab(bbox=(f.left, f.top, f.right, f.bottom), all_screens=True)
    else:
        img = ImageGrab.grab(all_screens=True)
    return await ocr_image(img)


# ----------------------------------------------------------------- clipboard
def get_clipboard() -> str:
    import pyperclip
    try:
        return pyperclip.paste() or ""
    except Exception:  # noqa: BLE001
        return ""


def set_clipboard(text: str):
    import pyperclip
    pyperclip.copy(text)


def copy_selection(w: Window | None) -> str:
    """Ctrl+C in the target window and return what was copied (restores nothing: the user asked for it)."""
    from jarvis.hands import actions as A
    before = get_clipboard()
    set_clipboard("")
    if w is not None:
        A.focus(w)
        time.sleep(0.15)
    A.press("ctrl", "c")
    for _ in range(10):
        time.sleep(0.05)
        text = get_clipboard()
        if text:
            return text
    set_clipboard(before)
    return ""


# -------------------------------------------------------------------- typing
INPUT_KEYBOARD, KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 1, 0x0004, 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def type_text(text: str, w: Window | None = None):
    """Type text into the focused (or given) window as real Unicode key presses."""
    from jarvis.hands import actions as A
    if w is not None:
        A.focus(w)
        time.sleep(0.2)
    for ch in text:
        if ch == "\n":
            A.press("enter") if "enter" in A.VK else None
            continue
        code = ord(ch)
        down = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)))
        up = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)))
        arr = (INPUT * 2)(down, up)
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.008)


async def run_blocking(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
