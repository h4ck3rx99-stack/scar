"""Reading the screen (Windows' built-in OCR engine, fully offline), clipboard, and typing text."""
import asyncio
import ctypes
import time
from ctypes import wintypes

from PIL import ImageGrab

from jarvis.hands.windows import Window

user32 = ctypes.windll.user32


async def _recognize(img):
    """Run Windows.Media.Ocr (ships with Windows, no download, no network). Returns (result, scale) where
    scale maps the engine's pixel coordinates back to the original image."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    img = img.convert("RGBA")
    w0 = img.width
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
    return await engine.recognize_async(bmp), w0 / img.width


async def ocr_image(img) -> str:
    result, _ = await _recognize(img)
    return "\n".join(line.text for line in result.lines)


async def ocr_find(w: Window | None, query: str):
    """Where is this text on screen? Best-matching OCR line or run of words -> (text, (left, top, right, bottom))
    in screen pixels, or None. Used to click things accessibility can't see (web page content, games, custom UIs)."""
    from rapidfuzz import fuzz
    if w is not None and not w.minimized:
        f = w.frame
        ox, oy = f.left, f.top
        img = ImageGrab.grab(bbox=(f.left, f.top, f.right, f.bottom), all_screens=True)
    else:
        ox = oy = 0
        img = ImageGrab.grab(all_screens=False)
    result, k = await _recognize(img)
    q = query.lower().strip()
    nq = len(q.split())
    best, best_score = None, 0
    for line in result.lines:
        words = list(line.words)
        # every run of up to (query words + 1) consecutive words, so "first video" can match inside a long line
        for i in range(len(words)):
            for j in range(i + 1, min(len(words), i + nq + 1) + 1):
                run = words[i:j]
                text = " ".join(x.text for x in run)
                score = fuzz.ratio(q, text.lower())
                if score > best_score:
                    x1 = min(x.bounding_rect.x for x in run)
                    y1 = min(x.bounding_rect.y for x in run)
                    x2 = max(x.bounding_rect.x + x.bounding_rect.width for x in run)
                    y2 = max(x.bounding_rect.y + x.bounding_rect.height for x in run)
                    best, best_score = (text, (int(ox + x1 * k), int(oy + y1 * k), int(ox + x2 * k), int(oy + y2 * k))), score
    return best if best_score >= 80 else None


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


PASTING = {"text": None}   # what Scar itself put on the clipboard (clipboard history ignores it)


def type_text(text: str, w: Window | None = None, paste: bool = False):
    """Type text into the focused (or given) window.
    Code and longer text is pasted, not typed: editors auto-indent after every Enter, so typing a multi-line
    snippet key by key doubles its indentation. Pasting keeps it exactly as written; the clipboard is restored."""
    from jarvis.hands import actions as A
    if w is not None:
        for _ in range(3):
            A.focus(w)
            time.sleep(0.2)
            fg = user32.GetForegroundWindow()
            if fg and (fg == w.hwnd or user32.GetAncestor(fg, 3) == w.hwnd or user32.GetAncestor(w.hwnd, 3) == fg):
                break
        else:
            # Typing into whatever else happens to be in front could put text somewhere private: refuse instead.
            raise RuntimeError(f"couldn't bring {w.spoken_name} to the front")
    enter = text.endswith("\n")
    body = text[:-1] if enter else text
    if paste or "\n" in body or len(body) > 120:
        old = get_clipboard()
        PASTING["text"] = body
        set_clipboard(body)
        time.sleep(0.05)
        A.press("ctrl", "v")
        time.sleep(0.35)          # let the app read the clipboard before it changes back
        set_clipboard(old)
        if enter:
            A.press("enter")
        return
    for ch in text:
        if ch == "\n":
            A.press("enter")
            continue
        code = ord(ch)
        down = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)))
        up = INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)))
        arr = (INPUT * 2)(down, up)
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.008)


async def run_blocking(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
