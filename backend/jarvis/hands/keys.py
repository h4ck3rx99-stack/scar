"""Named keyboard actions ("copy", "go back", "zoom in"...), scrolling, and Settings pages."""
import ctypes
import os
import time

user32 = ctypes.windll.user32
KEYEVENTF_KEYUP = 0x2
MOUSEEVENTF_WHEEL = 0x0800

VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "plus": 0xBB, "minus": 0xBD, "zero": 0x30,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    ".": 0xBE,
}
VK.update({str(d): ord(str(d)) for d in range(10)})                          # digits
VK.update({chr(c): ord(chr(c).upper()) for c in range(ord("a"), ord("z") + 1)})  # letters

# Spoken name -> key chord. Covers what people ask a desktop assistant to "do for them".
ACTIONS = {
    "copy": ("ctrl", "c"), "paste": ("ctrl", "v"), "cut": ("ctrl", "x"), "undo": ("ctrl", "z"), "redo": ("ctrl", "y"),
    "select_all": ("ctrl", "a"), "save": ("ctrl", "s"), "find": ("ctrl", "f"), "print": ("ctrl", "p"),
    "new_tab": ("ctrl", "t"), "close_tab": ("ctrl", "w"), "reopen_tab": ("ctrl", "shift", "t"),
    "next_tab": ("ctrl", "tab"), "previous_tab": ("ctrl", "shift", "tab"), "new_window": ("ctrl", "n"),
    "refresh": ("f5",), "back": ("alt", "left"), "forward": ("alt", "right"), "home_page": ("alt", "home"),
    "zoom_in": ("ctrl", "plus"), "zoom_out": ("ctrl", "minus"), "zoom_reset": ("ctrl", "zero"),
    "fullscreen": ("f11",), "switch_window": ("alt", "tab"), "show_desktop": ("win", "d"), "task_view": ("win", "tab"),
    "snip": ("win", "shift", "s"), "emoji": ("win", "."), "clipboard_history": ("win", "v"), "file_explorer": ("win", "e"),
    "enter": ("enter",), "escape": ("esc",), "space": ("space",), "tab": ("tab",), "backspace": ("backspace",),
    "delete": ("delete",), "page_down": ("pagedown",), "page_up": ("pageup",), "top": ("ctrl", "home"), "bottom": ("ctrl", "end"),
    "address_bar": ("ctrl", "l"), "bookmark": ("ctrl", "d"), "history": ("ctrl", "h"), "downloads_page": ("ctrl", "j"),
    "rename": ("f2",), "properties": ("alt", "enter"), "minimize_window": ("win", "down"), "maximize_window": ("win", "up"),
    "snap_left": ("win", "left"), "snap_right": ("win", "right"), "notifications": ("win", "n"), "quick_settings": ("win", "a"),
    "search": ("win", "s"), "run": ("win", "r"), "settings": ("win", "i"), "task_manager": ("ctrl", "shift", "esc"),
    "caption": ("c",), "youtube_play_pause": ("k",), "youtube_forward": ("l",), "youtube_back": ("j",),
    "youtube_captions": ("c",), "youtube_theater": ("t",), "youtube_fullscreen": ("f",), "youtube_mute": ("m",),
}

SETTINGS_PAGES = {
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi",
    "network": "ms-settings:network", "display": "ms-settings:display", "sound": "ms-settings:sound",
    "battery": "ms-settings:batterysaver", "power": "ms-settings:powersleep", "apps": "ms-settings:appsfeatures",
    "update": "ms-settings:windowsupdate", "windows update": "ms-settings:windowsupdate", "privacy": "ms-settings:privacy",
    "notifications": "ms-settings:notifications", "personalization": "ms-settings:personalization",
    "background": "ms-settings:personalization-background", "themes": "ms-settings:themes", "mouse": "ms-settings:mousetouchpad",
    "keyboard": "ms-settings:keyboard", "storage": "ms-settings:storagesense", "about": "ms-settings:about",
    "time": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage", "microphone": "ms-settings:privacy-microphone",
    "camera": "ms-settings:privacy-webcam", "vpn": "ms-settings:network-vpn", "hotspot": "ms-settings:network-mobilehotspot",
    "night light": "ms-settings:nightlight", "default apps": "ms-settings:defaultapps", "printers": "ms-settings:printers",
    "accounts": "ms-settings:yourinfo", "focus": "ms-settings:quiethours", "gaming": "ms-settings:gaming-gamebar",
}


def press(*keys):
    codes = [VK[k] for k in keys]
    for c in codes:
        user32.keybd_event(c, 0, 0, 0)
        time.sleep(0.01)
    for c in reversed(codes):
        user32.keybd_event(c, 0, KEYEVENTF_KEYUP, 0)


def do_action(name: str, times: int = 1):
    chord = ACTIONS[name]
    for _ in range(max(1, min(times, 20))):
        press(*chord)
        time.sleep(0.05)


def scroll(direction: str, amount: int = 5):
    """Mouse-wheel scroll wherever the pointer is (usually the window you're looking at)."""
    notches = max(1, min(int(amount), 30))
    delta = 120 if direction == "up" else -120
    for _ in range(notches):
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.02)


def open_settings(page: str):
    key = page.lower().replace(" settings", "").strip()
    uri = SETTINGS_PAGES.get(key)
    if not uri:
        from rapidfuzz import process
        m = process.extractOne(key, SETTINGS_PAGES.keys(), score_cutoff=80)
        uri = SETTINGS_PAGES[m[0]] if m else "ms-settings:"
    os.startfile(uri)
    return uri
