"""Application index (C9.4): Start Menu shortcuts, App Paths, packaged apps (Get-StartApps), PATH, user aliases.

Cached in SQLite with invalidation on Start Menu changes or after 24 h.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("scar.apps")

CACHE_KEY = "app_index.v1"
MAX_AGE = 24 * 3600


@dataclass
class AppEntry:
    name: str
    kind: str  # shortcut | app_paths | packaged | path | alias
    target: str  # exe path, lnk path, or AppUserModelID for packaged apps
    args: str = ""
    lnk: str = ""
    exe_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# common spoken names -> canonical executable names
SYNONYMS: dict[str, list[str]] = {
    "vs code": ["code.exe", "visual studio code"], "vscode": ["code.exe", "visual studio code"],
    "visual studio code": ["code.exe"], "code": ["code.exe"], "chrome": ["chrome.exe", "google chrome"],
    "google chrome": ["chrome.exe"], "edge": ["msedge.exe", "microsoft edge"], "microsoft edge": ["msedge.exe"],
    "firefox": ["firefox.exe"], "notepad": ["notepad.exe"], "calculator": ["calculator", "calc.exe"],
    "calc": ["calculator", "calc.exe"], "explorer": ["explorer.exe", "file explorer"], "file explorer": ["explorer.exe"],
    "terminal": ["windows terminal", "wt.exe"], "windows terminal": ["wt.exe"], "powershell": ["powershell.exe"],
    "cmd": ["cmd.exe"], "command prompt": ["cmd.exe"], "task manager": ["taskmgr.exe"], "paint": ["mspaint.exe", "paint"],
    "word": ["winword.exe", "word"], "excel": ["excel.exe", "excel"], "outlook": ["outlook.exe", "outlook"],
    "spotify": ["spotify.exe", "spotify"], "discord": ["discord"], "whatsapp": ["whatsapp"], "telegram": ["telegram"],
    "settings": ["settings"], "snipping tool": ["snipping tool", "snippingtool.exe"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


class AppIndex:
    def __init__(self, db: Any) -> None:
        self.db = db
        self._entries: list[AppEntry] = []
        self._built_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ build
    @staticmethod
    def _start_menu_dirs() -> list[Path]:
        dirs = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
                Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs"]
        return [d for d in dirs if d.exists()]

    def _fingerprint(self) -> float:
        latest = 0.0
        for d in self._start_menu_dirs():
            for root, _dirs, _files in os.walk(d):
                try:
                    latest = max(latest, os.path.getmtime(root))
                except OSError:
                    continue
        return latest

    def _shortcuts(self) -> list[AppEntry]:
        out: list[AppEntry] = []
        lnks = [p for d in self._start_menu_dirs() for p in d.rglob("*.lnk")]
        if not lnks or sys.platform != "win32":
            return out
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            sc = None
            for lnk in lnks:
                name = lnk.stem
                if re.search(r"(?i)\b(uninstall|readme|help|website|documentation|release notes)\b", name):
                    continue
                try:
                    sc = shell.CreateShortcut(str(lnk))
                    target = str(sc.TargetPath or "")
                    args = str(sc.Arguments or "")
                except Exception:  # noqa: BLE001 - broken shortcuts raise COM errors
                    target, args = "", ""
                out.append(AppEntry(name=name, kind="shortcut", target=target or str(lnk), args=args, lnk=str(lnk),
                                    exe_name=Path(target).name.lower() if target.lower().endswith(".exe") else ""))
                del sc
            del shell
        finally:
            pythoncom.CoUninitialize()
        return out

    @staticmethod
    def _app_paths() -> list[AppEntry]:
        if sys.platform != "win32":
            return []
        import winreg

        out: list[AppEntry] = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(key, sub) as sk:
                            val, _ = winreg.QueryValueEx(sk, "")
                    except OSError:
                        continue
                    exe = os.path.expandvars(str(val).strip('"'))
                    out.append(AppEntry(name=Path(sub).stem, kind="app_paths", target=exe, exe_name=sub.lower()))
        return out

    @staticmethod
    def _packaged() -> list[AppEntry]:
        ps = shutil.which("powershell")
        if ps is None:
            return []
        try:
            proc = subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-Command",
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            items = json.loads(proc.stdout or "[]")
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            log.warning("get_startapps_failed", error=str(exc))
            return []
        if isinstance(items, dict):
            items = [items]
        return [AppEntry(name=str(i.get("Name", "")), kind="packaged", target=str(i.get("AppID", ""))) for i in items
                if i.get("Name") and i.get("AppID")]

    @staticmethod
    def _path_exes() -> list[AppEntry]:
        out: list[AppEntry] = []
        seen: set[str] = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            try:
                for f in Path(d).glob("*.exe"):
                    n = f.name.lower()
                    if n not in seen:
                        seen.add(n)
                        out.append(AppEntry(name=f.stem, kind="path", target=str(f), exe_name=n))
            except OSError:
                continue
        code = shutil.which("code")
        if code and "code.cmd" not in seen:
            out.append(AppEntry(name="code", kind="path", target=code, exe_name="code.cmd"))
        return out

    def build(self, force: bool = False) -> int:
        with self._lock:
            fp = self._fingerprint()
            if not force and self._entries and time.time() - self._built_at < MAX_AGE:
                return len(self._entries)
            cached = self.db.kv_get(CACHE_KEY) if self.db is not None else None
            if not force and cached and cached.get("fingerprint") == fp and time.time() - cached.get("built", 0) < MAX_AGE:
                self._entries = [AppEntry(**e) for e in cached["entries"]]
                self._built_at = cached["built"]
                return len(self._entries)
            entries = self._shortcuts() + self._app_paths() + self._packaged() + self._path_exes()
            self._entries = entries
            self._built_at = time.time()
            if self.db is not None:
                self.db.kv_set(CACHE_KEY, {"fingerprint": fp, "built": self._built_at, "entries": [e.as_dict() for e in entries]})
            return len(entries)

    # ------------------------------------------------------------------ query
    def search(self, query: str, limit: int = 8, aliases: dict[str, str] | None = None) -> list[tuple[AppEntry, float]]:
        self.build()
        q = _norm(query)
        if aliases and q in aliases:
            return [(AppEntry(name=query, kind="alias", target=aliases[q]), 1.0)]
        wanted = [q, *[_norm(s) for s in SYNONYMS.get(q, [])]]
        scored: dict[str, tuple[AppEntry, float]] = {}
        for e in self._entries:
            n = _norm(e.name)
            exe = e.exe_name.lower()
            best = 0.0
            for w in wanted:
                if not w:
                    continue
                if w in (exe, n):
                    best = max(best, 1.0)
                elif n.startswith(w + " ") or exe.startswith(w.replace(" ", "")):
                    best = max(best, 0.85)
                elif w in n.split() or f" {w} " in f" {n} ":
                    best = max(best, 0.75)
                else:
                    r = difflib.SequenceMatcher(None, w, n).ratio()
                    if r > 0.72:
                        best = max(best, r * 0.8)
            if best > 0:
                # prefer Start Menu shortcuts and packaged apps (what users see) over raw PATH entries
                bonus = {"shortcut": 0.04, "packaged": 0.03, "app_paths": 0.02, "path": 0.0, "alias": 0.05}[e.kind]
                key = (e.target or e.name).lower()
                if key not in scored or scored[key][1] < best + bonus:
                    scored[key] = (e, min(1.0, best + bonus))
        return sorted(scored.values(), key=lambda t: t[1], reverse=True)[:limit]

    @property
    def size(self) -> int:
        return len(self._entries)


def vscode_cli() -> str | None:
    code = shutil.which("code")
    if code:
        return code
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", "")):
        for p in (Path(base) / "Programs/Microsoft VS Code/bin/code.cmd", Path(base) / "Microsoft VS Code/bin/code.cmd"):
            if p.exists():
                return str(p)
    return None
