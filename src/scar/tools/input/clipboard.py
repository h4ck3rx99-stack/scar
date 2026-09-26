"""Clipboard access via win32clipboard (text + images)."""

from __future__ import annotations

import io
import time
from pathlib import Path

from scar.core.errors import ToolError


def _open() -> None:
    import win32clipboard

    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception:  # noqa: BLE001 - pywintypes.error when another app holds the clipboard
            time.sleep(0.05)
    raise ToolError("the clipboard is busy (held by another application)", "ClipboardBusy")


def get_text() -> str | None:
    import win32clipboard
    import win32con

    _open()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
        return None
    finally:
        win32clipboard.CloseClipboard()


def set_text(text: str) -> None:
    import win32clipboard
    import win32con

    _open()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def set_image(path: Path) -> None:
    import win32clipboard
    from PIL import Image

    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "BMP")
    dib = buf.getvalue()[14:]  # strip BITMAPFILEHEADER
    _open()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def has_image() -> bool:
    import win32clipboard

    _open()
    try:
        return bool(win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB))
    finally:
        win32clipboard.CloseClipboard()
