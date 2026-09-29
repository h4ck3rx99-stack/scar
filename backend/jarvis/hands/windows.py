"""Finding top-level windows. All coordinates are physical screen pixels (per-monitor DPI aware)."""
import ctypes
import os
import re
from ctypes import wintypes
from dataclasses import dataclass

from rapidfuzz import fuzz

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
kernel32 = ctypes.windll.kernel32

# Must run before any window API call, otherwise every rect is DPI-virtualised on scaled displays.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
GW_OWNER = 4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Our own processes never count as targets.
SELF_EXES = {"electron.exe", "jarvis.exe", "python.exe", "pythonw.exe", "llama-server.exe"}
IGNORED_TITLES = {"Program Manager", "Windows Input Experience", "Microsoft Text Input Application"}
FRIENDLY = {"mspaint.exe": "Paint", "opera.exe": "Opera", "chrome.exe": "Chrome", "msedge.exe": "Edge", "code.exe": "VS Code", "explorer.exe": "File Explorer",
            "windowsterminal.exe": "Terminal", "whatsapp.root.exe": "WhatsApp", "winword.exe": "Word", "powerpnt.exe": "PowerPoint"}

# Spoken name -> (exe names, title keyword). Title keyword lets "youtube" find the browser window showing it.
ALIASES = {
    "chrome": (["chrome.exe"], None), "google chrome": (["chrome.exe"], None), "browser": (["opera.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"], None),
    "opera": (["opera.exe"], None), "opera gx": (["opera.exe"], None),
    "edge": (["msedge.exe"], None), "microsoft edge": (["msedge.exe"], None), "firefox": (["firefox.exe"], None), "brave": (["brave.exe"], None),
    "code": (["code.exe"], None), "vs code": (["code.exe"], None), "vscode": (["code.exe"], None), "visual studio code": (["code.exe"], None),
    "editor": (["code.exe", "notepad.exe"], None), "code editor": (["code.exe"], None),
    "notepad": (["notepad.exe"], None), "spotify": (["spotify.exe"], None), "discord": (["discord.exe"], None),
    "explorer": (["explorer.exe"], None), "file explorer": (["explorer.exe"], None), "files": (["explorer.exe"], None), "folder": (["explorer.exe"], None),
    "terminal": (["windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe"], None), "command prompt": (["cmd.exe", "windowsterminal.exe"], None),
    "powershell": (["powershell.exe", "pwsh.exe", "windowsterminal.exe"], None),
    "whatsapp": (["whatsapp.exe", "whatsapp.root.exe"], None), "word": (["winword.exe"], None), "excel": (["excel.exe"], None),
    "powerpoint": (["powerpnt.exe"], None), "teams": (["ms-teams.exe", "teams.exe"], None), "outlook": (["olk.exe", "outlook.exe"], None),
    "claude": (["claude.exe"], None), "calculator": ([], "calculator"), "settings": ([], "settings"),
    "youtube": ([], "youtube"), "gmail": ([], "gmail"), "github": ([], "github"), "netflix": ([], "netflix"),
}


@dataclass
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def as_dict(self):
        return {"x": self.left, "y": self.top, "width": self.width, "height": self.height}


@dataclass
class Window:
    hwnd: int
    title: str
    exe: str
    pid: int

    @property
    def rect(self) -> Rect:
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return Rect(r.left, r.top, r.right, r.bottom)

    @property
    def frame(self) -> Rect:
        """Visible bounds, without the invisible 7-8 px resize borders Windows 10/11 add."""
        r = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(self.hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            return Rect(r.left, r.top, r.right, r.bottom)
        return self.rect

    @property
    def minimized(self):
        return bool(user32.IsIconic(self.hwnd))

    @property
    def maximized(self):
        return bool(user32.IsZoomed(self.hwnd))

    @property
    def alive(self):
        return bool(user32.IsWindow(self.hwnd))

    @property
    def dpi_scale(self):
        return user32.GetDpiForWindow(self.hwnd) / 96 or 1.0

    @property
    def spoken_name(self):
        return FRIENDLY.get(self.exe) or (os.path.splitext(self.exe)[0].title() if self.exe else self.title[:30])

    def to_dict(self):
        return {"hwnd": hex(self.hwnd), "title": self.title, "app": self.spoken_name, "exe": self.exe,
                "minimized": self.minimized, "maximized": self.maximized, "bounds": self.frame.as_dict()}


def _exe_for_pid(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def _is_cloaked(hwnd):
    v = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return v.value != 0


def list_windows(include_self=False):
    """Visible, user-facing top-level windows in z-order (topmost first)."""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _is_cloaked(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if title in IGNORED_TITLES:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _exe_for_pid(pid.value)
        if not include_self and (exe in SELF_EXES or pid.value == os.getpid()):
            return True
        out.append(Window(int(hwnd), title, exe, pid.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def foreground_window():
    hwnd = user32.GetForegroundWindow()
    for w in list_windows():
        if w.hwnd == hwnd:
            return w
    wins = list_windows()
    return wins[0] if wins else None


_FILLER = re.compile(r"\b(the|my|a|window|app|application|tab|please)\b")


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", _FILLER.sub(" ", name.lower())).strip()


def find_window(query: str):
    """Best match for a spoken app/window name, or None. Prefers the topmost matching window."""
    q = normalize(query)
    wins = list_windows()
    if not q or q in ("this", "current", "active", "it", "that"):
        return foreground_window()
    if q in ALIASES:
        exes, kw = ALIASES[q]
        for w in wins:
            if kw and kw in w.title.lower():
                return w
        for w in wins:
            if w.exe in exes:
                return w
    best, best_score = None, 0
    for w in wins:
        stem = os.path.splitext(w.exe)[0]
        score = max(
            fuzz.partial_ratio(q, w.title.lower()) if len(q) > 2 else 0,
            fuzz.ratio(q, stem),
            100 if q == stem else 0,
        )
        if score > best_score:
            best, best_score = w, score
    return best if best_score >= 80 else None
