"""Shared filesystem helpers: guarded path resolution, hashing, backups, git detection."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from scar.core.errors import PathViolation
from scar.security.path_guard import PathCheck, PathOp
from scar.tools.base import ToolContext

TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
             ".csv", ".tsv", ".log", ".xml", ".html", ".htm", ".css", ".scss", ".sh", ".ps1", ".bat", ".cmd", ".c", ".h",
             ".cpp", ".hpp", ".cs", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".sql", ".r", ".swift", ".lua", ".vue",
             ".svelte", ".env.example", ".gitignore", ".dockerfile", ".mdx", ".rst", ".tex", ".conf", ".properties"}


def check_path(ctx: ToolContext, raw: str, op: PathOp) -> PathCheck:
    """Canonicalise and classify; raise PathViolation when the guard denies outright."""
    guard = ctx.services.path_guard
    chk: PathCheck = guard.check(raw, op, base=_cwd(ctx))
    if chk.denied:
        raise PathViolation(chk.canonical, "; ".join(chk.reasons) or "denied")
    return chk


def _cwd(ctx: ToolContext) -> str | None:
    cwd = ctx.services.extras.get("cwd") if ctx.services is not None else None
    return str(cwd) if cwd else None


def sha256_file(path: Path, limit_bytes: int = 512 * 1024 * 1024) -> str | None:
    if not path.is_file() or path.stat().st_size > limit_bytes:
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_root(path: Path) -> Path | None:
    p = path if path.is_dir() else path.parent
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def is_git_tracked(path: Path) -> bool:
    return git_root(path) is not None


def backup_file(ctx: ToolContext, path: Path, max_backups: int = 500, max_age_days: float = 30.0) -> Path | None:
    """Timestamped copy under <data>/backups before editing a file outside a git repo."""
    if not path.is_file() or is_git_tracked(path):
        return None
    root: Path = ctx.services.settings.backups_path
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    digest = hashlib.sha1(str(path).lower().encode()).hexdigest()[:10]  # noqa: S324 - filename bucketing only
    dest = root / f"{stamp}_{digest}_{path.name}"
    shutil.copy2(path, dest)
    _prune_backups(root, max_backups, max_age_days)
    return dest


def _prune_backups(root: Path, max_backups: int, max_age_days: float) -> None:
    files = sorted((p for p in root.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - max_age_days * 86400
    for p in files:
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
    files = [p for p in files if p.exists()]
    for p in files[: max(0, len(files) - max_backups)]:
        p.unlink(missing_ok=True)


def looks_binary(path: Path, probe: int = 4096) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(probe)
    except OSError:
        return False
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
        return False
    except UnicodeDecodeError:
        # latin-1 text is still text if mostly printable
        printable = sum(32 <= b < 127 or b in (9, 10, 13) for b in chunk)
        return printable < len(chunk) * 0.85


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def expand(raw: str) -> str:
    return os.path.expandvars(os.path.expanduser(raw))
