"""Daemon restart check: memory, scheduler, monitors and notifications survive a daemon restart.

    SCAR_LIVE_TESTS=1 uv run python scripts/acceptance/daemon_restart_check.py

1. start the daemon; through IPC: remember an alias, set a reminder due in 45 s, watch a helper process
2. stop the daemon before the reminder is due; let the helper process exit while the daemon is down
3. wait past the due time; start the daemon again
4. verify: the alias is still remembered, the missed reminder was reported and notified, the process monitor was
   reported as interrupted, and task history persisted. Results go to docs/daemon_restart_results.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.settings import load_settings  # noqa: E402
from scar.runtime.ipc import IpcClient, daemon_alive  # noqa: E402
from scar.storage.db import Database  # noqa: E402


def scar(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["uv", "run", "scar", *args], cwd=ROOT, capture_output=True, text=True, timeout=180,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


async def submit(settings, objective: str) -> dict:  # type: ignore[no-untyped-def]
    c = IpcClient(settings.data_path)
    assert await c.connect()
    try:
        first = await c.request("submit", objective=objective, interactive=False)
        assert first and first["type"] == "accepted", first
        async for msg in c.stream():
            if msg.get("type") == "result":
                return msg["task"]
        raise RuntimeError("daemon closed the stream")
    finally:
        await c.close()


async def main() -> int:
    if os.environ.get("SCAR_LIVE_TESTS") != "1":
        print("set SCAR_LIVE_TESTS=1")
        return 2
    settings = load_settings()
    sandbox = Path.home() / "scar-sandbox" / f"daemon-{time.strftime('%Y%m%d-%H%M%S')}"
    sandbox.mkdir(parents=True)
    out: dict = {"sandbox": str(sandbox)}
    if await daemon_alive(settings.data_path):
        scar("daemon", "stop")
    r = scar("daemon", "start")
    out["start1"] = r.stdout.strip()
    assert await daemon_alive(settings.data_path), r.stdout + r.stderr
    marker = f"restart-check-{int(time.time())}"
    out["remember"] = await submit(settings, f"remember that my {marker} folder is at {sandbox}")
    out["remind"] = await submit(settings, f"remind me in 30 seconds to {marker}")
    helper = subprocess.Popen([getattr(sys, "_base_executable", None) or sys.executable, "-c",
                               "import time, sys; time.sleep(20); sys.exit(5)"])
    out["watch"] = await submit(settings, f"watch process {helper.pid} and tell me if it crashes")
    r = scar("daemon", "stop")
    out["stopped_before_due"] = True
    out["stop"] = r.stdout.strip()
    stopped_at = time.time()
    helper.wait(60)
    time.sleep(max(0.0, 150 - (time.time() - stopped_at)))  # due + more than the 90 s grace period
    r = scar("daemon", "start")
    out["start2"] = r.stdout.strip()
    await asyncio.sleep(4)
    # verification
    status = await IpcClient(settings.data_path).connect()
    out["daemon_up_after_restart"] = status
    db = Database(settings.db_path)
    try:
        alias = db.query("SELECT text FROM memories WHERE category = 'alias' AND text LIKE ?", (f"%{marker}%",))
        sched = db.query("SELECT status, missed_count, fired_count FROM schedules WHERE text = ?", (marker,))
        monitor = db.query("SELECT status, target_json FROM monitors WHERE target_json LIKE ?", (f'%"pid": {helper.pid}%',))
        tasks = db.query("SELECT objective, status FROM tasks WHERE objective LIKE ? ORDER BY created_at", (f"%{marker}%",))
    finally:
        db.close()
    log = (settings.log_path / "scar.jsonl").read_text(encoding="utf-8", errors="replace")
    since = datetime.fromtimestamp(stopped_at, UTC).isoformat()  # only this run's notifications, not earlier runs'
    notified = [n for n in (json.loads(line) for line in log.splitlines()[-400:] if '"notification"' in line and "Missed" in line)
                if str(n.get("timestamp", "")) >= since]
    out.update({"alias_persisted": alias, "schedule": sched, "monitor": monitor, "tasks": tasks,
                "missed_notifications": [n.get("title") for n in notified]})
    ok = (bool(alias) and sched and sched[0]["missed_count"] >= 1 and sched[0]["status"] == "done"
          and monitor and monitor[0]["status"] == "interrupted" and bool(notified) and len(tasks) >= 2)
    out["status"] = "VERIFIED" if ok else "FAILED"
    scar("daemon", "stop")
    (ROOT / "docs" / "daemon_restart_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
