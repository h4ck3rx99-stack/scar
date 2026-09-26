"""On-disk artifact store for large tool outputs (C2, C8.9).

Artifacts are referenced by ``artifact://<task>/<id>`` handles and read back in
ranges, so large content never has to enter model context whole.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from pydantic import BaseModel

from scar.core.errors import ToolInputError
from scar.core.ids import new_id

_REF = re.compile(r"^artifact://(?P<task>[A-Za-z0-9_\-]+)/(?P<aid>[A-Za-z0-9_\-]+)(?P<ext>\.[a-z0-9]{1,5})?$")


class ArtifactInfo(BaseModel):
    ref: str
    path: str
    size: int
    lines: int | None = None


class ArtifactStore:
    def __init__(self, root: Path, max_total_bytes: int = 2 * 1024**3, max_age_days: float = 14.0) -> None:
        self.root = root
        self.max_total_bytes = max_total_bytes
        self.max_age_seconds = max_age_days * 86400
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, task_id: str, aid: str, ext: str) -> Path:
        safe_task = re.sub(r"[^A-Za-z0-9_\-]", "_", task_id or "none")
        return self.root / safe_task / f"{aid}{ext}"

    def put_text(self, task_id: str, text: str, ext: str = ".txt") -> ArtifactInfo:
        aid = new_id("art")
        path = self._path_for(task_id, aid, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8", errors="replace")
        path.write_bytes(data)
        return ArtifactInfo(
            ref=f"artifact://{path.parent.name}/{aid}{ext}", path=str(path), size=len(data), lines=text.count("\n") + 1
        )

    def put_bytes(self, task_id: str, data: bytes, ext: str = ".bin") -> ArtifactInfo:
        aid = new_id("art")
        path = self._path_for(task_id, aid, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return ArtifactInfo(ref=f"artifact://{path.parent.name}/{aid}{ext}", path=str(path), size=len(data))

    def resolve(self, ref: str) -> Path:
        m = _REF.match(ref.strip())
        if not m:
            raise ToolInputError(f"not an artifact reference: {ref!r}")
        path = self.root / m.group("task") / f"{m.group('aid')}{m.group('ext') or ''}"
        resolved = path.resolve()
        if self.root.resolve() not in resolved.parents:
            raise ToolInputError("artifact reference escapes the artifact store")
        if not resolved.exists():
            raise ToolInputError(f"artifact not found: {ref}")
        return resolved

    def read_lines(self, ref: str, start_line: int = 1, max_lines: int = 200, max_chars: int = 20_000) -> tuple[str, int]:
        """Return (text, total_lines) for a 1-based inclusive line window."""
        path = self.resolve(ref)
        out: list[str] = []
        total = 0
        used = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for total, line in enumerate(fh, start=1):
                if total < start_line or len(out) >= max_lines:
                    continue
                if used + len(line) > max_chars:
                    out.append(line[: max(0, max_chars - used)])
                    used = max_chars
                    continue
                if used >= max_chars:
                    continue
                out.append(line)
                used += len(line)
        return "".join(out), total

    def read_bytes(self, ref: str, offset: int = 0, length: int = 65536) -> bytes:
        path = self.resolve(ref)
        with path.open("rb") as fh:
            fh.seek(max(0, offset))
            return fh.read(max(0, min(length, 16 * 1024 * 1024)))

    def enforce_retention(self) -> int:
        """Delete artifacts older than the age limit, then oldest-first above the size cap."""
        with self._lock:
            now = time.time()
            files: list[tuple[float, int, Path]] = []
            removed = 0
            for dirpath, _dirs, names in os.walk(self.root):
                for name in names:
                    p = Path(dirpath) / name
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    if now - st.st_mtime > self.max_age_seconds:
                        p.unlink(missing_ok=True)
                        removed += 1
                    else:
                        files.append((st.st_mtime, st.st_size, p))
            total = sum(size for _, size, _ in files)
            for _mtime, size, p in sorted(files):
                if total <= self.max_total_bytes:
                    break
                p.unlink(missing_ok=True)
                total -= size
                removed += 1
            for dirpath, dirs, names in os.walk(self.root, topdown=False):
                if not dirs and not names and Path(dirpath) != self.root:
                    try:
                        Path(dirpath).rmdir()
                    except OSError:
                        continue
            return removed
