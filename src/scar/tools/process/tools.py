"""Process manager tools (C9.3): list, inspect, start, stop, wait."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from typing import Any

import psutil
from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path

CRITICAL_PROCESSES = {"csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "services.exe", "smss.exe", "system",
                      "svchost.exe", "lsaiso.exe", "dwm.exe", "fontdrvhost.exe", "registry", "memcompression",
                      "msmpeng.exe", "securityhealthservice.exe", "system idle process", "audiodg.exe", "sihost.exe"}


def _proc_row(p: psutil.Process) -> dict[str, Any]:
    with p.oneshot():
        try:
            mem = p.memory_info().rss / 2**20
        except psutil.Error:
            mem = 0.0
        try:
            exe = p.exe()
        except psutil.Error:
            exe = ""
        try:
            cmd = " ".join(p.cmdline())[:300]
        except psutil.Error:
            cmd = ""
        return {"pid": p.pid, "name": p.name(), "memory_mb": round(mem, 1), "status": p.status(), "exe": exe,
                "cmdline": cmd, "started": p.create_time()}


class ListInput(ToolInput):
    name: str | None = Field(None, description="filter by (substring of) process name")
    limit: int = Field(40, ge=1, le=500)


class ProcessList(Tool):
    name = "process.list"
    description = "List running processes (optionally filtered by name), with PID and memory."
    input_model = ListInput
    capabilities = ("process.read",)
    categories = ("process", "system")

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        def collect() -> list[dict[str, Any]]:
            rows = []
            for p in psutil.process_iter():
                try:
                    if args.name and args.name.lower() not in p.name().lower():
                        continue
                    rows.append(_proc_row(p))
                except psutil.Error:
                    continue
            rows.sort(key=lambda r: r["memory_mb"], reverse=True)
            return rows[: args.limit]

        rows = await asyncio.to_thread(collect)
        view = "\n".join(f"{r['pid']:>7} {r['name']:<32} {r['memory_mb']:>8.1f} MB" for r in rows)
        return self.ok(f"{len(rows)} processes", {"processes": rows}, model_view=view)


class InspectInput(ToolInput):
    pid: int


class ProcessInspect(Tool):
    name = "process.inspect"
    description = "Details for one process: exe, command line, memory, CPU, children, status."
    input_model = InspectInput
    capabilities = ("process.read",)
    categories = ("process",)

    async def run(self, args: InspectInput, ctx: ToolContext) -> ToolResult:
        try:
            p = psutil.Process(args.pid)
            row = await asyncio.to_thread(_proc_row, p)
            row["cpu_percent"] = p.cpu_percent(0.3)
            row["children"] = [c.pid for c in p.children(recursive=True)]
            row["managed_by_scar"] = ctx.services.processes.get(args.pid) is not None
        except psutil.NoSuchProcess as exc:
            raise ToolError(f"No process with PID {args.pid}", "NotFound") from exc
        except psutil.AccessDenied as exc:
            raise ToolError(f"Access denied to PID {args.pid}", "AccessDenied") from exc
        return self.ok(f"{row['name']} (PID {args.pid})", row)


class StartInput(ToolInput):
    program: str = Field(description="Executable path or name on PATH")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    managed: bool = Field(True, description="Track it (Job Object) so it can be stopped with the task")


class ProcessStart(Tool):
    name = "process.start"
    description = "Start a program with arguments (no shell). Returns its PID; verified running."
    input_model = StartInput
    capabilities = ("process.start",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("process",)
    requires = Requires(setting="pc_control_enabled")
    sensitive_args = {"program": "command", "args": "command", "cwd": "path"}

    def assess(self, args: StartInput, ctx: ToolContext) -> RiskAssessment:
        cls = ctx.services.command_guard.classify_argv([args.program, *args.args], cwd=args.cwd)
        a = RiskAssessment(max(cls.risk, RiskLevel.MEDIUM), list(cls.reasons))
        if cls.denied:
            a.deny("; ".join(cls.reasons))
        a.facts.command = " ".join([args.program, *args.args])
        a.facts.app = os.path.splitext(os.path.basename(args.program))[0].lower()
        return a

    def describe(self, args: StartInput) -> str:
        return f"start {args.program} {' '.join(args.args)}".strip()

    async def run(self, args: StartInput, ctx: ToolContext) -> ToolResult:
        exe = shutil.which(args.program) or args.program
        if os.path.sep in exe or (len(exe) > 1 and exe[1] == ":"):
            exe = str(check_path(ctx, exe, PathOp.EXECUTE).path)
        if not os.path.exists(exe) and shutil.which(exe) is None:
            raise ToolError(f"program not found: {args.program}", "NotFound")
        cwd = str(check_path(ctx, args.cwd, PathOp.EXECUTE).path) if args.cwd else None
        pm = ctx.services.processes
        if args.managed:
            _popen, mp = await asyncio.to_thread(pm.popen, [exe, *args.args], cwd=cwd, name=os.path.basename(exe),
                                                 owner_task=None, kind="app", new_console=True)
            pid = mp.pid
        else:
            import subprocess

            proc = await asyncio.to_thread(subprocess.Popen, [exe, *args.args], cwd=cwd,
                                           creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            pid = proc.pid
        return self.ok(f"Started {os.path.basename(exe)} (PID {pid})", {"pid": pid, "program": exe, "managed": args.managed})

    async def verify(self, args: StartInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        await asyncio.sleep(0.8)
        pid = result.data["pid"]
        alive = psutil.pid_exists(pid)
        return VerificationResult.from_checks([Check(name="process running", passed=alive, detail=f"pid {pid}")])


class StopInput(ToolInput):
    pid: int | None = None
    name: str | None = Field(None, description="stop all processes with this exact name, e.g. notepad.exe")
    force: bool = Field(False, description="kill immediately instead of asking the process to exit")


class ProcessStop(Tool):
    name = "process.stop"
    description = "Stop a process by PID or exact name. System-critical processes are refused."
    input_model = StopInput
    capabilities = ("process.kill",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    categories = ("process",)
    requires = Requires(setting="pc_control_enabled")

    def _targets(self, args: StopInput) -> list[psutil.Process]:
        if args.pid is None and not args.name:
            raise ToolInputError("give pid or name")
        if args.pid is not None:
            try:
                return [psutil.Process(args.pid)]
            except psutil.NoSuchProcess as exc:
                raise ToolError(f"No process with PID {args.pid}", "NotFound") from exc
        wanted = (args.name or "").lower()
        if not wanted.endswith(".exe"):
            wanted += ".exe"
        out = []
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() == wanted:
                out.append(p)
        return out

    def assess(self, args: StopInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.HIGH, ["terminates processes"])
        try:
            targets = self._targets(args)
        except (ToolError, ToolInputError):
            return a
        names = set()
        for p in targets:
            try:
                names.add(p.name().lower())
            except psutil.Error:
                continue
        if names & CRITICAL_PROCESSES or any(p.pid in (0, 4) for p in targets):
            a.deny("refuses to stop a system-critical process", "kill_critical_process")
        if os.getpid() in [p.pid for p in targets]:
            a.deny("refuses to stop SCAR itself")
        a.facts.app = next(iter(names), None)
        a.facts.file_count = len(targets)
        return a

    def describe(self, args: StopInput) -> str:
        return f"{'force-kill' if args.force else 'stop'} " + (f"PID {args.pid}" if args.pid else f"all '{args.name}' processes")

    async def run(self, args: StopInput, ctx: ToolContext) -> ToolResult:
        targets = self._targets(args)
        if not targets:
            raise ToolError(f"no running process named {args.name}", "NotFound")
        stopped: list[int] = []
        for p in targets:
            try:
                if ctx.services.processes.get(p.pid) is not None:
                    ctx.services.processes.kill(p.pid)
                elif args.force:
                    p.kill()
                else:
                    p.terminate()
                stopped.append(p.pid)
            except psutil.NoSuchProcess:
                stopped.append(p.pid)
            except psutil.AccessDenied as exc:
                raise ToolError(f"access denied stopping PID {p.pid} (it may run elevated)", "AccessDenied") from exc
        _gone, alive = await asyncio.to_thread(psutil.wait_procs, targets, 5)
        if alive and not args.force:
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    continue
            await asyncio.to_thread(psutil.wait_procs, alive, 3)
        return self.ok(f"Stopped {len(stopped)} process(es)", {"pids": stopped})

    async def verify(self, args: StopInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name=f"PID {pid} exited", passed=not psutil.pid_exists(pid) or
                                                     psutil.Process(pid).status() == psutil.STATUS_ZOMBIE)
                                               for pid in result.data["pids"]])


class WaitInput(ToolInput):
    pid: int
    timeout_s: float = Field(60, gt=0, le=3600)


class ProcessWait(Tool):
    name = "process.wait"
    description = "Wait (event-based, not polling) for a process to exit; returns its exit code if SCAR started it."
    input_model = WaitInput
    capabilities = ("process.read",)
    categories = ("process",)
    timeout = 3700.0
    timeout_field = "timeout_s"

    async def run(self, args: WaitInput, ctx: ToolContext) -> ToolResult:
        from scar.tools.monitor.waits import wait_for_pid_exit

        t0 = time.monotonic()
        code = await wait_for_pid_exit(args.pid, args.timeout_s, ctx.cancel)
        if code is None and psutil.pid_exists(args.pid):
            return self.ok(f"PID {args.pid} still running after {args.timeout_s:.0f}s", {"pid": args.pid, "exited": False})
        return self.ok(f"PID {args.pid} exited" + (f" with code {code}" if code is not None else ""),
                       {"pid": args.pid, "exited": True, "exit_code": code, "waited_s": round(time.monotonic() - t0, 1)})


TOOLS: list[type[Tool]] = [ProcessList, ProcessInspect, ProcessStart, ProcessStop, ProcessWait]
