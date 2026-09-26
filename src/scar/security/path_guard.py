"""Filesystem guard (C4.7).

Every path is canonicalised before any check: environment variables and ``~``
expanded, ``\\\\?\\`` prefixes stripped, relative segments resolved, symlinks,
junctions and reparse points followed (``os.path.realpath``), 8.3 short names
expanded, alternate data streams split off, and comparison done
case-insensitively.
"""

from __future__ import annotations

import ntpath
import os
import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from scar.core.types import PolicyDecision, RiskLevel


class PathOp(StrEnum):
    READ = "read"
    LIST = "list"
    WRITE = "write"
    CREATE = "create"
    DELETE = "delete"
    DELETE_PERMANENT = "delete_permanent"
    MOVE = "move"
    EXECUTE = "execute"


class PathCategory(StrEnum):
    ALLOWED = "allowed"  # inside a configured allowed root
    OUTSIDE = "outside"  # not protected, not inside an allowed root
    SYSTEM = "system"  # Windows / Program Files / boot areas
    PROTECTED = "protected"  # user-configured protected paths
    SECRET = "secret"  # credentials, keys, browser profiles, SCAR secrets -> always DENY
    DEVICE = "device"  # \\.\ device namespace, reserved names
    NETWORK = "network"  # UNC path to another machine


_WRITE_OPS = {PathOp.WRITE, PathOp.CREATE, PathOp.DELETE, PathOp.DELETE_PERMANENT, PathOp.MOVE}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class PathCheck:
    original: str
    canonical: str
    category: PathCategory
    op: PathOp
    risk: RiskLevel
    decision_floor: PolicyDecision | None
    reasons: list[str] = field(default_factory=list)
    stream: str | None = None

    @property
    def denied(self) -> bool:
        return self.decision_floor == PolicyDecision.DENY

    @property
    def path(self) -> Path:
        return Path(self.canonical)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _long_path(p: str) -> str:
    """Expand 8.3 short names on the longest existing prefix (Windows only)."""
    if sys.platform != "win32" or "~" not in p:
        return p
    import ctypes

    get_long = ctypes.windll.kernel32.GetLongPathNameW
    head, tail = p, ""
    while head and not os.path.exists(head):
        new_head, part = ntpath.split(head)
        if new_head == head:
            break
        tail = ntpath.join(part, tail) if tail else part
        head = new_head
    if not head:
        return p
    buf = ctypes.create_unicode_buffer(32768)
    n = get_long(head, buf, 32768)
    if n == 0 or n > 32768:
        return p
    return ntpath.join(buf.value, tail) if tail else buf.value


def split_stream(path: str) -> tuple[str, str | None]:
    """Split ``C:\\x\\file.txt:stream:$DATA`` into (path, stream)."""
    body = path
    prefix = ""
    m = re.match(r"^[A-Za-z]:", body)
    if m:
        prefix, body = body[:2], body[2:]
    if ":" in body:
        base, _, stream = body.partition(":")
        return prefix + base, stream or None
    return path, None


def normcase(p: str) -> str:
    return ntpath.normcase(ntpath.normpath(p))


def is_within(child: str, parent: str) -> bool:
    c, p = normcase(child), normcase(parent)
    if c == p:
        return True
    return c.startswith(p.rstrip("\\/") + "\\")


def canonicalize(raw: str, base: str | Path | None = None) -> tuple[str, str | None, list[str]]:
    """Return (canonical_path, stream, notes)."""
    notes: list[str] = []
    p = raw.strip().strip('"')
    if not p:
        raise ValueError("empty path")
    if "\x00" in p:
        raise ValueError("path contains NUL byte")
    p = os.path.expandvars(os.path.expanduser(p))
    p = p.replace("/", "\\") if sys.platform == "win32" else p
    lowered = p.lower()
    if lowered.startswith("\\\\?\\unc\\"):
        p = "\\\\" + p[8:]
        notes.append("stripped \\\\?\\UNC prefix")
    elif lowered.startswith("\\\\?\\") or lowered.startswith("\\??\\"):
        p = p[4:]
        notes.append("stripped \\\\?\\ prefix")
    elif lowered.startswith("\\\\.\\"):
        return p, None, ["device namespace path"]
    p, stream = split_stream(p)
    if stream:
        notes.append(f"alternate data stream {stream!r}")
    if not ntpath.isabs(p) and not os.path.isabs(p):
        p = os.path.join(str(base) if base else os.getcwd(), p)
    p = _long_path(p)
    try:
        resolved = os.path.realpath(p)
    except (OSError, ValueError):
        resolved = os.path.abspath(p)
    if sys.platform == "win32":
        low = resolved.lower()
        if low.startswith("\\\\?\\unc\\"):
            resolved = "\\\\" + resolved[8:]
        elif low.startswith("\\\\?\\"):
            resolved = resolved[4:]
    if normcase(resolved) != normcase(os.path.abspath(p)):
        notes.append("resolved link/junction/short name")
    return resolved, stream, notes


def default_secret_paths(scar_data_dir: Path | None = None, scar_config_dir: Path | None = None) -> list[str]:
    home = str(Path.home())
    appdata = _env("APPDATA", os.path.join(home, "AppData", "Roaming"))
    local = _env("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
    paths = [
        os.path.join(home, ".ssh"),
        os.path.join(home, ".gnupg"),
        os.path.join(home, ".aws"),
        os.path.join(home, ".azure"),
        os.path.join(home, ".kube"),
        os.path.join(home, ".docker", "config.json"),
        os.path.join(home, ".git-credentials"),
        os.path.join(home, ".netrc"),
        os.path.join(appdata, "Microsoft", "Credentials"),
        os.path.join(local, "Microsoft", "Credentials"),
        os.path.join(appdata, "Microsoft", "Protect"),
        os.path.join(local, "Microsoft", "Vault"),
        os.path.join(appdata, "Microsoft", "Vault"),
        os.path.join(local, "Google", "Chrome", "User Data"),
        os.path.join(local, "Microsoft", "Edge", "User Data"),
        os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"),
        os.path.join(appdata, "Mozilla", "Firefox", "Profiles"),
        os.path.join(appdata, "Opera Software"),
        os.path.join(local, "Vivaldi", "User Data"),
        os.path.join(appdata, "gh"),
        os.path.join(appdata, "Telegram Desktop", "tdata"),
    ]
    if scar_data_dir is not None:
        paths += [
            str(scar_data_dir / "ipc.json"),
            str(scar_data_dir / "browser-profile"),
            str(scar_data_dir / "telethon"),
        ]
    if scar_config_dir is not None:
        paths.append(str(scar_config_dir / ".env"))
    return paths


def default_system_paths() -> list[str]:
    windir = _env("WINDIR", "C:\\Windows")
    sysdrive = _env("SystemDrive", "C:") + "\\"
    return [
        windir,
        _env("ProgramFiles", "C:\\Program Files"),
        _env("ProgramFiles(x86)", "C:\\Program Files (x86)"),
        _env("ProgramW6432", "C:\\Program Files"),
        os.path.join(_env("ProgramData", "C:\\ProgramData"), "Microsoft"),
        os.path.join(sysdrive, "Recovery"),
        os.path.join(sysdrive, "$Recycle.Bin"),
        os.path.join(sysdrive, "System Volume Information"),
        os.path.join(sysdrive, "Boot"),
        os.path.join(sysdrive, "EFI"),
        os.path.join(sysdrive, "pagefile.sys"),
        os.path.join(sysdrive, "hiberfil.sys"),
        os.path.join(sysdrive, "swapfile.sys"),
    ]


_SECRET_FILE_NAMES = re.compile(
    r"(?i)^(\.env(\.(?!example$|sample$|template$|dist$)[^.]+)?|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|.*\.(pem|pfx|p12|kdbx|ppk)|login data|cookies|web data|key[34]\.db|logins\.json)$"
)


class PathGuard:
    def __init__(
        self,
        allowed_roots: list[Path],
        protected_paths: list[str] | None = None,
        *,
        scar_data_dir: Path | None = None,
        scar_config_dir: Path | None = None,
        project_env_files_allowed: bool = False,
    ) -> None:
        self.allowed_roots = [canonicalize(str(r))[0] for r in allowed_roots]
        self.protected = [canonicalize(p)[0] for p in (protected_paths or [])]
        self.secret = [canonicalize(p)[0] for p in default_secret_paths(scar_data_dir, scar_config_dir)]
        self.system = [canonicalize(p)[0] for p in default_system_paths()]
        self.scar_data_dir = canonicalize(str(scar_data_dir))[0] if scar_data_dir else None
        self.project_env_files_allowed = project_env_files_allowed

    def categorize(self, canonical: str) -> tuple[PathCategory, list[str]]:
        reasons: list[str] = []
        if canonical.startswith("\\\\.\\") or canonical.lower().startswith("\\\\.\\"):
            return PathCategory.DEVICE, ["device namespace"]
        name = ntpath.basename(canonical)
        stem = name.split(".")[0].lower()
        if stem in _RESERVED:
            return PathCategory.DEVICE, [f"reserved device name {name}"]
        for s in self.secret:
            if is_within(canonical, s):
                return PathCategory.SECRET, [f"inside secret store {s}"]
        if _SECRET_FILE_NAMES.match(name) and not self.project_env_files_allowed:
            return PathCategory.SECRET, [f"secret-bearing file name {name}"]
        if canonical.startswith("\\\\"):
            parts = canonical[2:].split("\\")
            host = parts[0].lower() if parts else ""
            if host in {"localhost", "127.0.0.1", ".", "?"} or (len(parts) > 1 and parts[1].endswith("$")):
                return PathCategory.SYSTEM, ["administrative/loopback share"]
            return PathCategory.NETWORK, [f"network path on {host}"]
        for p in self.protected:
            if is_within(canonical, p):
                return PathCategory.PROTECTED, [f"inside protected path {p}"]
        for s in self.system:
            if is_within(canonical, s):
                return PathCategory.SYSTEM, [f"inside system path {s}"]
        for r in self.allowed_roots:
            if is_within(canonical, r):
                return PathCategory.ALLOWED, reasons
        return PathCategory.OUTSIDE, ["outside allowed roots"]

    def check(self, raw: str | Path, op: PathOp, base: str | Path | None = None) -> PathCheck:
        try:
            canonical, stream, notes = canonicalize(str(raw), base)
        except ValueError as exc:
            return PathCheck(str(raw), str(raw), PathCategory.DEVICE, op, RiskLevel.CRITICAL, PolicyDecision.DENY, [str(exc)])
        category, reasons = self.categorize(canonical)
        reasons = notes + reasons
        risk, floor = self._risk(category, op)
        if stream:
            if op in _WRITE_OPS:
                risk = max(risk, RiskLevel.HIGH)
                reasons.append("writes to an alternate data stream")
            elif stream.lower() not in {"$data", ""}:
                risk = max(risk, RiskLevel.MEDIUM)
        return PathCheck(str(raw), canonical, category, op, risk, floor, reasons, stream)

    @staticmethod
    def _risk(category: PathCategory, op: PathOp) -> tuple[RiskLevel, PolicyDecision | None]:
        write = op in _WRITE_OPS
        if category in (PathCategory.SECRET, PathCategory.DEVICE):
            return RiskLevel.CRITICAL, PolicyDecision.DENY
        if op == PathOp.DELETE_PERMANENT:
            return RiskLevel.CRITICAL, None
        if category == PathCategory.SYSTEM:
            return (RiskLevel.CRITICAL, None) if write or op == PathOp.EXECUTE else (RiskLevel.MEDIUM, None)
        if category == PathCategory.PROTECTED:
            return (RiskLevel.CRITICAL, None) if write else (RiskLevel.HIGH, None)
        if category == PathCategory.NETWORK:
            return (RiskLevel.HIGH, None) if write else (RiskLevel.MEDIUM, None)
        if category == PathCategory.OUTSIDE:
            if op in (PathOp.DELETE, PathOp.MOVE):
                return RiskLevel.HIGH, None
            return (RiskLevel.HIGH, None) if write else (RiskLevel.MEDIUM, None)
        # ALLOWED
        if op in (PathOp.DELETE, PathOp.MOVE):
            return RiskLevel.HIGH, None
        if op in (PathOp.WRITE, PathOp.CREATE):
            return RiskLevel.MEDIUM, None
        if op == PathOp.EXECUTE:
            return RiskLevel.MEDIUM, None
        return RiskLevel.LOW, None

    def is_secret(self, raw: str | Path) -> bool:
        return self.check(raw, PathOp.READ).category == PathCategory.SECRET
