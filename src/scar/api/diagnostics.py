"""Diagnostics for the app: log queries, the redacted diagnostic bundle, and live resource snapshots."""

from __future__ import annotations

import asyncio
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from scar.security.redaction import global_redactor

LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def read_logs(settings: Any, *, task: str | None, level: str | None, limit: int) -> list[dict[str, Any]]:
    """Newest-last log entries (already redacted at write time; redacted again here as defence in depth)."""
    red = global_redactor()
    if task:
        from scar.observability.tracing import Tracer

        return [json.loads(red.redact(json.dumps(e, default=str))) for e in Tracer(settings.log_path / "traces").read(task)[-limit:]]
    path = settings.log_path / "scar.jsonl"
    if not path.exists():
        return []
    floor = LEVELS.get((level or "debug").lower(), 10)
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - 4 * 1024 * 1024))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            e = json.loads(red.redact(line))
        except ValueError:
            continue
        if LEVELS.get(str(e.get("level", "info")), 20) < floor:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return list(reversed(out))


def resource_snapshot(services: Any) -> dict[str, Any]:
    import psutil

    from scar.resources.monitor import sample

    snap = sample()
    me = psutil.Process()
    try:
        rss = me.memory_info().rss + sum(c.memory_info().rss for c in me.children(recursive=True))
    except psutil.Error:
        rss = me.memory_info().rss
    gpu = snap.gpus[0] if snap.gpus else None
    models = services.local_models.status() if services.local_models is not None else []
    return {"cpu_percent": round(snap.cpu_percent, 1), "ram_used_mb": round(snap.ram_used_mb),
            "ram_total_mb": round(snap.ram_total_mb), "scar_rss_mb": round(rss / 2**20, 1),
            "gpu": None if gpu is None else {"name": gpu.name, "util_percent": gpu.util_percent,
                                              "vram_used_mb": round(gpu.mem_used_mb), "vram_total_mb": round(gpu.mem_total_mb)},
            "battery_percent": snap.battery_percent, "on_ac": snap.on_ac,
            "local_models": [{"name": str(mm.get("name", "")), "idle_s": mm.get("idle_s")} for mm in models]}


async def build_bundle(services: Any) -> Path:
    """A zip for bug reports: doctor results, status, recent logs and task traces, all redacted. Never secrets,
    never the database, memory contents or screenshots."""
    from scar.cli.doctor import Doctor
    from scar.cli.status import collect_status

    red = global_redactor()
    out_dir = services.settings.data_path / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"scar-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    doctor = [{"ok": r.ok, "label": r.label, "hint": r.hint, "section": r.section}
              for r in await Doctor(services.settings, services).run()]
    status = collect_status(services, services.tasks)
    status.pop("recent_tasks", None)  # objectives can be personal; the logs show what is needed
    logs = read_logs(services.settings, task=None, level="info", limit=2000)

    def write() -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doctor.json", red.redact(json.dumps(doctor, indent=2, default=str)))
            z.writestr("status.json", red.redact(json.dumps(status, indent=2, default=str)))
            z.writestr("logs.jsonl", red.redact("\n".join(json.dumps(e, default=str) for e in logs)))
            z.writestr("README.txt", "SCAR diagnostic bundle. Secrets are redacted; no database, memories or screenshots "
                                     "are included. Review before sharing.\n")

    await asyncio.to_thread(write)
    return path
