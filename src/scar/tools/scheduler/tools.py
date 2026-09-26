"""Scheduler, monitor and notification tools (C9.12)."""

from __future__ import annotations

import os
import shlex
from datetime import datetime
from typing import Literal

from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path
from scar.tools.scheduler.timeparse import parse_when


def _fmt(dt_iso: str) -> str:
    return datetime.fromisoformat(dt_iso).astimezone().strftime("%a %d %b %H:%M")


class ReminderInput(ToolInput):
    text: str = Field(description="what to remind about")
    when: str = Field(description="natural language: 'in 10 minutes', 'tomorrow at 9am', 'every monday 10am'")


class ScheduleReminder(Tool):
    name = "schedule.reminder"
    description = "Create a reminder (one-shot or recurring). Fires as a toast, spoken (voice mode) and in the CLI."
    input_model = ReminderInput
    capabilities = ("schedule.write",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("schedule",)

    def describe(self, args: ReminderInput) -> str:
        return f"remind '{args.text}' {args.when}"

    async def run(self, args: ReminderInput, ctx: ToolContext) -> ToolResult:
        try:
            pt = parse_when(args.when, ctx.services.settings.timezone)
        except ValueError as exc:
            raise ToolError(str(exc), "BadTime") from exc
        row = ctx.services.scheduler.add("reminder", args.text, pt.when, interval_s=pt.interval_s, tz=ctx.services.settings.timezone)
        rec = f" (repeats every {int(pt.interval_s)}s)" if pt.interval_s else ""
        return self.ok(f"I'll remind you at {_fmt(row['next_run'])}{rec}", {"schedule": row})

    async def verify(self, args: ReminderInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        row = ctx.services.scheduler.get(result.data["schedule"]["schedule_id"])
        return VerificationResult.from_checks([Check(name="schedule persisted", passed=row is not None and row["status"] == "active")])


class TaskScheduleInput(ToolInput):
    objective: str = Field(description="what SCAR should do at that time")
    when: str


class ScheduleTask(Tool):
    name = "schedule.task"
    description = "Schedule SCAR to carry out an objective later (one-shot or recurring). Runs in the background with normal permissions."
    input_model = TaskScheduleInput
    capabilities = ("schedule.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("schedule",)

    def describe(self, args: TaskScheduleInput) -> str:
        return f"schedule task '{args.objective}' {args.when}"

    async def run(self, args: TaskScheduleInput, ctx: ToolContext) -> ToolResult:
        try:
            pt = parse_when(args.when, ctx.services.settings.timezone)
        except ValueError as exc:
            raise ToolError(str(exc), "BadTime") from exc
        row = ctx.services.scheduler.add("task", args.objective, pt.when, interval_s=pt.interval_s, objective=args.objective,
                                         tz=ctx.services.settings.timezone)
        return self.ok(f"Scheduled for {_fmt(row['next_run'])}", {"schedule": row})


class ListInput(ToolInput):
    include_done: bool = False


class ScheduleList(Tool):
    name = "schedule.list"
    description = "List reminders and scheduled tasks."
    input_model = ListInput
    capabilities = ("schedule.read",)
    categories = ("schedule",)

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        rows = ctx.services.scheduler.list(args.include_done)
        view = "\n".join(f"{r['schedule_id']} [{r['kind']}/{r['status']}] {_fmt(r['next_run'])}: {r['text']}" for r in rows)
        return self.ok(f"{len(rows)} scheduled item(s)", {"schedules": rows}, model_view=view or "nothing scheduled")


class CancelInput(ToolInput):
    id: str


class ScheduleCancel(Tool):
    name = "schedule.cancel"
    description = "Cancel a reminder or scheduled task."
    input_model = CancelInput
    capabilities = ("schedule.write",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("schedule",)

    async def run(self, args: CancelInput, ctx: ToolContext) -> ToolResult:
        if not ctx.services.scheduler.cancel(args.id):
            raise ToolError(f"no active schedule {args.id}", "NotFound")
        return self.ok("Cancelled", {"id": args.id})


# ---------------------------------------------------------------- monitors
class MonitorInput(ToolInput):
    kind: Literal["process", "folder", "command", "url", "download"]
    pid: int | None = Field(None, description="process: PID to watch")
    process_name: str | None = Field(None, description="process: name, if PID unknown (first match)")
    notify_on: Literal["crash", "any_exit"] = "crash"
    path: str | None = Field(None, description="folder/download: folder to watch; command: working directory")
    pattern: str = "*"
    recursive: bool = False
    command: str | None = Field(None, description="command: command line to run and watch")
    url: str | None = None
    timeout_s: float = Field(3600, gt=0, le=7 * 86400)


class MonitorStart(Tool):
    name = "monitor.start"
    description = ("Watch something and notify: a process (crash/exit, event-based), a folder (changes), a command "
                   "(completion), a URL (comes up), or the downloads folder (download finished).")
    input_model = MonitorInput
    capabilities = ("monitor.write",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("monitor", "process", "dev")
    sensitive_args = {"path": "path", "command": "command", "url": "url"}

    def assess(self, args: MonitorInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW)
        if args.kind == "command" and args.command:
            cls = ctx.services.command_guard.classify(args.command, "powershell", cwd=args.path)
            a.raise_to(max(cls.risk, RiskLevel.MEDIUM), "; ".join(cls.reasons))
            if cls.denied:
                a.deny("; ".join(cls.reasons))
            a.facts.command = args.command
        if args.path:
            chk = ctx.services.path_guard.check(args.path, PathOp.LIST)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
        return a

    def describe(self, args: MonitorInput) -> str:
        target = args.pid or args.process_name or args.path or args.command or args.url
        return f"watch {args.kind} {target}"

    async def run(self, args: MonitorInput, ctx: ToolContext) -> ToolResult:
        svc = ctx.services.monitors
        try:
            if args.kind == "process":
                pid = args.pid
                if pid is None and args.process_name:
                    import psutil

                    want = args.process_name.lower().removesuffix(".exe")
                    pid = next((p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().removesuffix(".exe") == want), None)
                if pid is None:
                    raise ToolError("no such process to watch", "NotFound")
                m = svc.watch_process(pid, crash_only=args.notify_on == "crash")
            elif args.kind in ("folder", "download"):
                folder = args.path or (str(ctx.services.settings.downloads_path) if args.kind == "download" else None)
                if not folder:
                    raise ToolError("path is required", "InvalidInput")
                folder = str(check_path(ctx, folder, PathOp.LIST).path)
                m = (svc.watch_download(folder, pattern=args.pattern, timeout_s=args.timeout_s) if args.kind == "download"
                     else svc.watch_folder(folder, pattern=args.pattern, recursive=args.recursive))
            elif args.kind == "command":
                if not args.command:
                    raise ToolError("command is required", "InvalidInput")
                from scar.tools.terminal.runner import shell_argv

                cwd = str(check_path(ctx, args.path, PathOp.EXECUTE).path) if args.path else (ctx.services.extras.get("cwd") or os.getcwd())
                argv, _ = shell_argv(args.command, "powershell")
                m = svc.watch_command(argv, cwd, timeout_s=args.timeout_s, label=shlex.split(args.command, posix=False)[0][:40])
            else:
                if not args.url:
                    raise ToolError("url is required", "InvalidInput")
                m = svc.watch_url(args.url, timeout_s=args.timeout_s)
        except ValueError as exc:
            raise ToolError(str(exc), "MonitorError") from exc
        return self.ok(f"Watching {args.kind} ({m.id}); I'll notify you", {"id": m.id, "kind": m.kind, "target": m.target})

    async def verify(self, args: MonitorInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        m = ctx.services.monitors.monitors.get(result.data["id"])
        return VerificationResult.from_checks([Check(name="monitor running", passed=m is not None and m.task is not None
                                                     and (not m.task.done() or m.status != "active"))])


class MonitorListInput(ToolInput):
    pass


class MonitorList(Tool):
    name = "monitor.list"
    description = "List monitors and their status."
    input_model = MonitorListInput
    capabilities = ("monitor.read",)
    categories = ("monitor",)

    async def run(self, args: MonitorListInput, ctx: ToolContext) -> ToolResult:
        rows = ctx.services.monitors.list()
        return self.ok(f"{len(rows)} monitor(s)", {"monitors": rows},
                       model_view="\n".join(f"{r['id']} [{r['kind']}/{r['status']}] {r['target']}" for r in rows) or "none")


class MonitorCancel(Tool):
    name = "monitor.cancel"
    description = "Stop a monitor."
    input_model = CancelInput
    capabilities = ("monitor.write",)
    side_effects = SideEffect.LOCAL
    categories = ("monitor",)

    async def run(self, args: CancelInput, ctx: ToolContext) -> ToolResult:
        if not ctx.services.monitors.cancel(args.id):
            raise ToolError(f"no active monitor {args.id}", "NotFound")
        return self.ok("Monitor stopped", {"id": args.id})


class NotifyInput(ToolInput):
    title: str = "SCAR"
    message: str
    speak: bool = True


class NotifySend(Tool):
    name = "notify.send"
    description = "Show a Windows notification (and speak it in voice mode)."
    input_model = NotifyInput
    capabilities = ("notify",)
    side_effects = SideEffect.LOCAL
    categories = ("notify", "schedule", "monitor")

    async def run(self, args: NotifyInput, ctx: ToolContext) -> ToolResult:
        d = await ctx.services.notifier.notify(args.title, args.message, task_id=ctx.task_id, speak=args.speak)
        return self.ok(f"Notified via {', '.join(d.channels)}", {"channels": d.channels})

    async def verify(self, args: NotifyInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name="delivered", passed=bool(result.data["channels"]),
                                                     detail=",".join(result.data["channels"]))])


TOOLS: list[type[Tool]] = [ScheduleReminder, ScheduleTask, ScheduleList, ScheduleCancel, MonitorStart, MonitorList,
                           MonitorCancel, NotifySend]
