"""System information and media/volume control (C9.3)."""

from __future__ import annotations

import asyncio
import socket
from typing import Any, Literal

import psutil
from pydantic import Field

from scar.core.types import RiskLevel, SideEffect, ToolResult
from scar.resources.monitor import sample
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput


class InfoInput(ToolInput):
    sections: list[Literal["cpu", "memory", "gpu", "battery", "disk", "network", "top", "os"]] = Field(
        default_factory=lambda: ["cpu", "memory", "gpu", "battery", "disk", "network", "top", "os"])
    top_n: int = Field(8, ge=1, le=50)
    sort_by: Literal["memory", "cpu"] = "memory"


def _top_processes(n: int, sort_by: str) -> list[dict[str, Any]]:
    procs = list(psutil.process_iter(["pid", "name", "memory_info", "username"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            continue
    import time

    time.sleep(0.5)
    rows: list[dict[str, Any]] = []
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / psutil.cpu_count()
            mem = p.info["memory_info"].rss / 2**20 if p.info.get("memory_info") else 0.0
        except psutil.Error:
            continue
        rows.append({"pid": p.pid, "name": p.info.get("name") or "?", "cpu_percent": round(cpu, 1), "memory_mb": round(mem, 1)})
    rows.sort(key=lambda r: r["memory_mb"] if sort_by == "memory" else r["cpu_percent"], reverse=True)
    # aggregate by name as users think of apps ("chrome" = many processes)
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["name"], {"name": r["name"], "processes": 0, "memory_mb": 0.0, "cpu_percent": 0.0})
        a["processes"] += 1
        a["memory_mb"] = round(a["memory_mb"] + r["memory_mb"], 1)
        a["cpu_percent"] = round(a["cpu_percent"] + r["cpu_percent"], 1)
    by_app = sorted(agg.values(), key=lambda r: r["memory_mb"] if sort_by == "memory" else r["cpu_percent"], reverse=True)
    return by_app[:n]


class SystemInfo(Tool):
    name = "system.info"
    description = "CPU, RAM, GPU/VRAM, battery, disks, network and the top apps by memory or CPU."
    input_model = InfoInput
    capabilities = ("system.read",)
    categories = ("system",)
    timeout = 30.0

    async def run(self, args: InfoInput, ctx: ToolContext) -> ToolResult:
        return await asyncio.to_thread(self._collect, args)

    def _collect(self, args: InfoInput) -> ToolResult:
        snap = sample(cpu_interval=0.3)
        data: dict[str, Any] = {}
        lines: list[str] = []
        s = set(args.sections)
        if "os" in s:
            import platform

            data["os"] = {"system": platform.system(), "release": platform.release(), "version": platform.version(),
                          "machine": platform.machine(), "hostname": socket.gethostname(),
                          "boot_time": psutil.boot_time()}
            lines.append(f"OS: {platform.system()} {platform.release()} ({platform.version()})")
        if "cpu" in s:
            data["cpu"] = {"percent": snap.cpu_percent, "cores": psutil.cpu_count(logical=False),
                           "threads": psutil.cpu_count()}
            lines.append(f"CPU: {snap.cpu_percent:.0f}% of {psutil.cpu_count()} threads")
        if "memory" in s:
            data["memory"] = {"total_mb": round(snap.ram_total_mb), "used_mb": round(snap.ram_used_mb),
                              "available_mb": round(snap.ram_available_mb)}
            lines.append(f"RAM: {snap.ram_used_mb / 1024:.1f} of {snap.ram_total_mb / 1024:.1f} GB used")
        if "gpu" in s:
            data["gpu"] = [g.__dict__ for g in snap.gpus]
            for g in snap.gpus:
                lines.append(f"GPU: {g.name} {g.util_percent:.0f}% busy, VRAM {g.mem_used_mb:.0f}/{g.mem_total_mb:.0f} MiB")
            if not snap.gpus:
                lines.append(f"GPU: {snap.nvml_error or 'no NVIDIA GPU'}")
        if "battery" in s:
            data["battery"] = {"percent": snap.battery_percent, "plugged_in": snap.on_ac}
            if snap.battery_percent is not None:
                lines.append(f"Battery: {snap.battery_percent:.0f}% ({'plugged in' if snap.on_ac else 'on battery'})")
        if "disk" in s:
            disks = []
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except (PermissionError, OSError):
                    continue
                disks.append({"mount": part.mountpoint, "total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1),
                              "percent": u.percent})
                lines.append(f"Disk {part.mountpoint} {u.free / 2**30:.0f} GB free of {u.total / 2**30:.0f} GB")
            data["disk"] = disks
        if "network" in s:
            stats = psutil.net_if_stats()
            up = [n for n, st in stats.items() if st.isup]
            online = _online()
            data["network"] = {"interfaces_up": up, "internet": online}
            lines.append(f"Network: {'online' if online else 'offline'} ({', '.join(up[:4])})")
        if "top" in s:
            top = _top_processes(args.top_n, args.sort_by)
            data["top"] = top
            lines.append(f"Top by {args.sort_by}: " + ", ".join(
                f"{t['name']} {t['memory_mb']:.0f} MB" if args.sort_by == "memory" else f"{t['name']} {t['cpu_percent']:.0f}%"
                for t in top))
        return self.ok(lines[0] if len(lines) == 1 else "System status collected", data, model_view="\n".join(lines))


def _online() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2).close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- media keys / volume
_VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
       "stop": 0xB2}


class MediaInput(ToolInput):
    action: Literal["volume_up", "volume_down", "mute", "play_pause", "next", "previous", "stop", "set_volume"]
    steps: int = Field(1, ge=1, le=50, description="repeat count for volume_up/down (each step is ~2%)")
    level: int | None = Field(None, ge=0, le=100, description="target volume percent for set_volume")


class MediaControl(Tool):
    name = "system.media"
    description = "Volume up/down/mute/set level and media play/pause/next/previous via media keys."
    input_model = MediaInput
    capabilities = ("system.media",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("system",)
    requires = Requires(platform="win32", setting="pc_control_enabled")

    def assess(self, args: MediaInput, ctx: ToolContext) -> RiskAssessment:
        return RiskAssessment(RiskLevel.LOW)

    def describe(self, args: MediaInput) -> str:
        return f"media {args.action}" + (f" to {args.level}%" if args.level is not None else "")

    async def run(self, args: MediaInput, ctx: ToolContext) -> ToolResult:
        from scar.tools.input.sendinput import press_vk

        if args.action == "set_volume":
            if args.level is None:
                return ToolResult.failure("set_volume needs level", "InvalidInput")
            # deterministic: drive to 0 then up in 2% steps
            await asyncio.to_thread(press_vk, _VK["volume_down"], 50)
            await asyncio.to_thread(press_vk, _VK["volume_up"], round(args.level / 2))
            return self.ok(f"Volume set to about {args.level}%", {"level": args.level})
        await asyncio.to_thread(press_vk, _VK[args.action], args.steps if args.action.startswith("volume") else 1)
        return self.ok(f"Media: {args.action.replace('_', ' ')}", {"action": args.action})


TOOLS: list[type[Tool]] = [SystemInfo, MediaControl]
