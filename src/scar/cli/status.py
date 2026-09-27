"""`scar status`: tasks, monitors, loaded models, resources, provider health, data egress, autonomy, grants."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.table import Table

from scar.cli.render import console
from scar.resources.monitor import sample
from scar.security.autonomy import AUTONOMY_DESCRIPTIONS


def collect_status(services: Any, tasks: Any | None) -> dict[str, Any]:
    snap = sample(cpu_interval=0.2)
    running = []
    if tasks is not None:
        running = [{"task_id": h.task.task_id, "objective": h.task.objective[:80], "status": h.task.status.value,
                    "background": h.task.background} for h in tasks.running.values()]
    recent = services.db.query("SELECT task_id, objective, status, result_summary, created_at FROM tasks WHERE parent_task_id IS NULL "
                               "ORDER BY created_at DESC LIMIT 8")
    monitors = services.monitors.list() if services.monitors is not None else services.db.query(
        "SELECT monitor_id AS id, kind, status FROM monitors ORDER BY created_at DESC LIMIT 20")
    now = datetime.now(UTC).timestamp()
    health = [{"provider": h.provider, "model": h.model, "state": h.state,
               "cooldown_s": max(0, int(h.cooldown_until - now)) if h.cooldown_until else 0,
               "latency_ms": round(h.latency_ewma_ms) if h.latency_ewma_ms else None, "error": h.last_error[:100]}
              for h in services.health.all()]
    egress_rows = services.db.query("SELECT provider, data_class FROM data_egress WHERE session_id = ?", (services.session_id,))
    egress: dict[str, list[str]] = {}
    for r in egress_rows:
        egress.setdefault(r["provider"], []).append(r["data_class"])
    grants = [{"id": g.grant_id, "effect": g.effect, "kind": g.kind.value, "tool": g.tool, "scope": g.scope.value,
               "match": g.match, "expires": g.expires_at.isoformat() if g.expires_at else None} for g in services.grants.list()]
    schedules = services.db.query("SELECT schedule_id, kind, text, next_run FROM schedules WHERE status = 'active' ORDER BY next_run LIMIT 10")
    return {
        "autonomy_level": services.settings.autonomy_level,
        "autonomy": AUTONOMY_DESCRIPTIONS.get(services.settings.autonomy_level, ""),
        "running_tasks": running,
        "recent_tasks": recent,
        "monitors": monitors,
        "schedules": schedules,
        "loaded_models": services.local_models.status(),
        "resources": {"cpu_percent": snap.cpu_percent, "ram_used_mb": round(snap.ram_used_mb), "ram_total_mb": round(snap.ram_total_mb),
                      "process_rss_mb": round(snap.process_rss_mb, 1), "battery": snap.battery_percent, "on_ac": snap.on_ac,
                      "gpu": [{"name": g.name, "util": g.util_percent, "vram_used_mb": round(g.mem_used_mb),
                               "vram_total_mb": round(g.mem_total_mb)} for g in snap.gpus]},
        "managed_processes": [{"pid": m.pid, "name": m.name, "kind": m.kind} for m in services.processes.list()],
        "provider_health": health,
        "data_sent_this_session": egress,
        "grants": grants,
        "metrics": services.metrics.session_counters(),
    }


def print_status(st: dict[str, Any]) -> None:
    console.print(f"[bold]Autonomy[/bold] level {st['autonomy_level']}: {st['autonomy']}")
    r = st["resources"]
    gpu = "; ".join(f"{g['name']} {g['util']:.0f}% VRAM {g['vram_used_mb']}/{g['vram_total_mb']} MiB" for g in r["gpu"]) or "no NVIDIA GPU"
    console.print(f"[bold]Resources[/bold] CPU {r['cpu_percent']:.0f}% · RAM {r['ram_used_mb']}/{r['ram_total_mb']} MiB · "
                  f"SCAR RSS {r['process_rss_mb']} MiB · {gpu}" + (f" · battery {r['battery']:.0f}%" if r["battery"] is not None else ""))
    if st["running_tasks"]:
        t = Table("task", "status", "objective", title="Running tasks", title_justify="left")
        for x in st["running_tasks"]:
            t.add_row(x["task_id"], x["status"], x["objective"])
        console.print(t)
    if st["recent_tasks"]:
        t = Table("task", "status", "objective", "result", title="Recent tasks", title_justify="left")
        for x in st["recent_tasks"]:
            t.add_row(x["task_id"][-10:], x["status"], (x["objective"] or "")[:40], (x["result_summary"] or "")[:60])
        console.print(t)
    else:
        console.print("[bold]Recent tasks[/bold] none yet")
    if st["monitors"]:
        t = Table("monitor", "kind", "status", title="Monitors", title_justify="left")
        for m in st["monitors"][:10]:
            t.add_row(m["id"], m["kind"], m["status"])
        console.print(t)
    if st["schedules"]:
        t = Table("id", "kind", "next", "text", title="Scheduled", title_justify="left")
        for s in st["schedules"]:
            t.add_row(s["schedule_id"][-10:], s["kind"], s["next_run"][:16], s["text"][:50])
        console.print(t)
    console.print("[bold]Loaded models[/bold] " + (", ".join(f"{m['name']} (idle {m['idle_s']}s)" for m in st["loaded_models"]) or "none"))
    if st["managed_processes"]:
        console.print("[bold]Managed processes[/bold] " + ", ".join(f"{p['name']}#{p['pid']} ({p['kind']})" for p in st["managed_processes"]))
    if st["provider_health"]:
        t = Table("provider", "state", "retry in", "latency", "last error", title="Provider health", title_justify="left")
        for h in st["provider_health"]:
            t.add_row(h["provider"] + (f"/{h['model']}" if h["model"] else ""), _STATE_WORDS.get(h["state"], h["state"]),
                      _retry_in(h["cooldown_s"]), f"{h['latency_ms']} ms" if h["latency_ms"] else "", h["error"])
        console.print(t)
    egress = st["data_sent_this_session"]
    console.print("[bold]Cloud providers that received data this session[/bold] " +
                  (", ".join(f"{p} ({', '.join(sorted(set(c)))})" for p, c in egress.items()) or "none"))
    console.print("[bold]Active permissions[/bold] " + (", ".join(f"{g['id']} {g['effect']} {g['tool'] or ''} {g['scope']}"
                                                              for g in st["grants"]) or "none"))


_STATE_WORDS = {"closed": "ok", "open": "paused after errors", "half_open": "retrying", "unavailable": "unavailable",
                "removed": "model withdrawn"}


def _retry_in(seconds: int) -> str:
    if not seconds:
        return ""
    if seconds > 86400:
        return "after the key is fixed"
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"
