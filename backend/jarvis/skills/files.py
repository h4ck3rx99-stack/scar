"""Folders and file search in the user's own folders."""
import os
import time
from pathlib import Path

from rapidfuzz import fuzz

HOME = Path.home()
FOLDERS = {
    "downloads": HOME / "Downloads", "download": HOME / "Downloads", "documents": HOME / "Documents",
    "desktop": HOME / "Desktop", "pictures": HOME / "Pictures", "photos": HOME / "Pictures", "music": HOME / "Music",
    "videos": HOME / "Videos", "home": HOME, "screenshots": HOME / "Pictures" / "Screenshots",
}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "AppData", ".venv", "venv", "site-packages", "$Recycle.Bin"}


def folder(name: str):
    key = name.lower().replace("my ", "").replace(" folder", "").strip()
    # OneDrive often redirects Documents/Desktop/Pictures.
    p = FOLDERS.get(key)
    if p and not p.exists():
        alt = HOME / "OneDrive" / p.name
        p = alt if alt.exists() else p
    return p if p and p.exists() else None


def open_path(p: Path):
    os.startfile(str(p))


def find(query: str, limit=5, budget_s=2.5):
    """Fuzzy filename search in Desktop, Documents, Downloads, Pictures, Videos, Music (time-boxed)."""
    q = query.lower().strip()
    roots = [p for p in (folder(n) for n in ("desktop", "documents", "downloads", "pictures", "videos", "music")) if p]
    hits = []
    t0 = time.monotonic()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                score = fuzz.partial_ratio(q, f.lower())
                if score >= 80:
                    p = Path(dirpath) / f
                    try:
                        hits.append((score, p.stat().st_mtime, p))
                    except OSError:
                        pass
            if time.monotonic() - t0 > budget_s:
                break
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return [h[2] for h in hits[:limit]]


def latest_download():
    d = folder("downloads")
    if not d:
        return None
    files = [p for p in d.iterdir() if p.is_file() and not p.name.endswith((".crdownload", ".part", ".tmp"))]
    return max(files, key=lambda p: p.stat().st_mtime, default=None)
