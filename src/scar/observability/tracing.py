"""Per-task tracing (C3).

Each task's trace records tool calls with timings, which provider/model served
each request and its latency, fallbacks, retries, errors, approval decisions,
resource snapshots and the final state. Traces are JSONL files under the log
directory (redacted), one file per task.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import structlog

from scar.core.events import Event, EventBus
from scar.security.redaction import global_redactor
from scar.storage.db import now_iso

log = structlog.get_logger("scar.trace")


class Tracer:
    def __init__(self, trace_dir: Path, max_files: int = 500) -> None:
        self.trace_dir = trace_dir
        self.max_files = max_files
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path_for(self, task_id: str) -> Path:
        return self.trace_dir / f"{task_id}.jsonl"

    def record(self, task_id: str | None, kind: str, **data: Any) -> None:
        if not task_id:
            return
        entry = {"at": now_iso(), "kind": kind, **global_redactor().redact_obj(data)}
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with self._lock:
            with self.path_for(task_id).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read(self, task_id: str) -> list[dict[str, Any]]:
        p = self.path_for(task_id)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def attach(self, bus: EventBus) -> None:
        def on_event(event: Event) -> None:
            if event.task_id:
                self.record(event.task_id, event.kind, **event.model_dump(exclude={"task_id", "at", "kind"}))

        bus.add_listener(on_event)

    def prune(self) -> int:
        files = sorted(self.trace_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        removed = 0
        while len(files) - removed > self.max_files:
            files[removed].unlink(missing_ok=True)
            removed += 1
        return removed
