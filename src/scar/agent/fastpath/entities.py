"""Entity resolution for the fast path: folders/projects by name.

Order: memory aliases → Windows Search index (Search.CollatorDSO) → Everything (es.exe) → bounded scan of allowed roots.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scar.security.path_guard import PathOp

_SKIP = {"node_modules", ".git", "$recycle.bin", "appdata", ".venv", "venv", "__pycache__", "windows", "program files",
         "program files (x86)", "programdata"}


@dataclass
class FolderCandidate:
    path: str
    source: str
    score: float


def _clean_name(phrase: str) -> str:
    s = phrase.strip().strip("\"'").rstrip(".")
    s = re.sub(r"(?i)^(my|the|our|this)\s+", "", s)
    s = re.sub(r"(?i)\s+(folder|directory|dir|project|repo|repository|workspace)$", "", s)
    return s.strip()


def _search_index(name: str, limit: int = 10) -> list[str]:
    if sys.platform != "win32":
        return []
    import pythoncom
    import win32com.client

    safe = name.replace("'", "''")
    pythoncom.CoInitialize()
    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(f"SELECT TOP {limit} System.ItemPathDisplay FROM SYSTEMINDEX WHERE System.ItemType = 'Directory' "
                f"AND System.FileName = '{safe}'", conn)
        out: list[str] = []
        while not rs.EOF:
            out.append(str(rs.Fields.Item("System.ItemPathDisplay").Value))
            rs.MoveNext()
        rs.Close()
        conn.Close()
        del rs, conn
        return out
    except Exception:  # noqa: BLE001 - the indexer may be disabled; fall through to other resolvers
        return []
    finally:
        pythoncom.CoUninitialize()


def _everything(name: str, limit: int = 10) -> list[str]:
    es = shutil.which("es") or shutil.which("es.exe")
    if es is None:
        return []
    try:
        out = subprocess.run([es, "-n", str(limit), "/ad", f"wfn:{name}"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _scan(roots: list[Path], name: str, max_depth: int = 5, budget_s: float = 4.0, limit: int = 10) -> list[str]:
    target = name.lower()
    found: list[str] = []
    deadline = time.monotonic() + budget_s
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirs, _files in os.walk(root):
            if time.monotonic() > deadline:
                return found
            depth = len(Path(dirpath).parts) - base_depth
            keep = []
            for d in dirs:
                dl = d.lower()
                if dl in _SKIP or dl.startswith("."):
                    continue
                if dl == target:
                    found.append(str(Path(dirpath) / d))
                    if len(found) >= limit:
                        return found
                keep.append(d)
            dirs[:] = keep if depth < max_depth else []
    return found


class FolderResolver:
    def __init__(self, services: Any) -> None:
        self.s = services

    def resolve(self, phrase: str) -> list[FolderCandidate]:
        raw = phrase.strip().strip("\"'")
        expanded = os.path.expandvars(os.path.expanduser(raw))
        if re.match(r"^[A-Za-z]:[\\/]|^\\\\|^~", raw) or os.path.isabs(expanded):
            p = Path(expanded)
            return [FolderCandidate(str(p), "path", 1.0)] if p.is_dir() else []
        mem = getattr(self.s, "memory", None)
        if mem is not None:
            target = mem.resolve_alias(raw, "folder")
            if target and Path(target).is_dir():
                return [FolderCandidate(target, "memory alias", 1.0)]
        name = _clean_name(raw)
        if not name:
            return []
        guard = self.s.path_guard
        roots = self.s.settings.effective_allowed_roots

        def allowed(p: str) -> bool:
            chk = guard.check(p, PathOp.LIST)
            return not chk.denied and chk.category.value in ("allowed", "outside")

        cands: dict[str, FolderCandidate] = {}
        for source, results in (("windows search", _search_index(name)), ("everything", _everything(name))):
            for p in results:
                if Path(p).is_dir() and allowed(p) and p.lower() not in cands:
                    cands[p.lower()] = FolderCandidate(p, source, 0.9)
            if cands:
                break
        if not cands:
            for p in _scan(list(roots), name):
                cands[p.lower()] = FolderCandidate(p, "scan", 0.8)
        # prefer folders inside the allowed roots, then shallower paths
        ranked = sorted(cands.values(), key=lambda c: (not any(c.path.lower().startswith(str(r).lower()) for r in roots),
                                                       len(Path(c.path).parts)))
        return ranked
