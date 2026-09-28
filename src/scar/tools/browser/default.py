"""The user's default web browser (Windows: HKCU UrlAssociations https UserChoice), for "open this website".

Automation never uses this browser or its profile; it uses SCAR's own Playwright profile (see manager.py).
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

NAMES = {
    "ChromeHTML": "Chrome", "MSEdgeHTM": "Edge", "MSEdgeBHTML": "Edge Beta", "BraveHTML": "Brave",
    "Opera GXStable": "Opera GX", "OperaStable": "Opera", "VivaldiHTM": "Vivaldi", "IE.HTTP": "Internet Explorer",
}
# a browser the user can name in a request, and the exe names that belong to it
NAMED = {"chrome": ("Chrome", ("chrome.exe",)), "edge": ("Edge", ("msedge.exe",)), "firefox": ("Firefox", ("firefox.exe",)),
         "opera": ("Opera", ("opera.exe",)), "brave": ("Brave", ("brave.exe",))}


@dataclass(frozen=True)
class Browser:
    prog_id: str
    name: str
    exe: str | None


def _reg(root: int, path: str, value: str = "") -> str | None:
    import winreg

    try:
        with winreg.OpenKey(root, path) as k:
            return str(winreg.QueryValueEx(k, value)[0])
    except OSError:
        return None


def _exe_from_command(cmd: str | None) -> str | None:
    if not cmd:
        return None
    try:
        first = shlex.split(cmd, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        return None
    return first if Path(first).exists() else None


def default_browser() -> Browser:
    if sys.platform != "win32":
        return Browser("", "your default browser", None)
    import winreg

    prog = _reg(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice", "ProgId") or ""
    name = NAMES.get(prog) or ("Firefox" if prog.startswith("FirefoxURL") else re.sub(r"(HTML?|URL|Stable)$", "", prog) or
                              "your default browser")
    exe = _exe_from_command(_reg(winreg.HKEY_CLASSES_ROOT, rf"{prog}\shell\open\command")) if prog else None
    return Browser(prog, name, exe)


def named_browser_exe(name: str) -> str | None:
    """Path of a browser the user named ("open chrome and go to …"), from the App Paths registry."""
    if sys.platform != "win32" or name not in NAMED:
        return None
    default = default_browser()
    if name == "opera" and default.exe and default.name.startswith("Opera"):
        return default.exe  # Opera and Opera GX share opera.exe; prefer the one the user actually uses
    import winreg

    for exe in NAMED[name][1]:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            p = _reg(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
            if p and Path(p.strip('"')).exists():
                return p.strip('"')
    return None


def _version_key(d: Path) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in d.name.split("."))
    except ValueError:
        return ()


def opera_gx_exe() -> str | None:
    """Opera GX's browser binary, for automation with SCAR's own profile. The versioned binary starts in under a
    second; the top-level opera.exe is an update-checking launcher (~6 s), used only when no versioned one exists."""
    if sys.platform != "win32":
        return None
    import os

    roots = []
    d = default_browser()
    if d.exe and d.name == "Opera GX":
        roots.append(Path(d.exe).parent)
    roots.append(Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Opera GX")))
    for root in roots:
        versions = sorted((v for v in root.glob("*") if (v / "opera.exe").exists() and _version_key(v)), key=_version_key)
        if versions:
            return str(versions[-1] / "opera.exe")
        if (root / "opera.exe").exists():
            return str(root / "opera.exe")
    return None
