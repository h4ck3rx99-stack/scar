"""Dev-server tools: start (with readiness detection), status, logs, stop."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.dev.project import detect_project
from scar.tools.fs.common import check_path
from scar.tools.terminal.runner import shell_argv

TERMINAL = Requires(setting="terminal_enabled")


class StartInput(ToolInput):
    path: str = Field(description="project folder")
    command: str | None = Field(None, description="command line; default: the project's dev/start script")
    ready_pattern: str | None = Field(None, description="regex that marks readiness in the output")
    url: str | None = Field(None, description="URL to probe; default: first localhost URL printed")
    wait_ready_s: float = Field(120, ge=0, le=900, description="0 = return immediately; readiness is reported later")
    notify_when_ready: bool = True


class DevServerStart(Tool):
    name = "devserver.start"
    description = ("Start a project's development server as a managed background process. Readiness = output pattern "
                   "AND an HTTP probe. Crashes are detected and reported.")
    input_model = StartInput
    capabilities = ("terminal.exec",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev",)
    requires = TERMINAL
    timeout = 960.0
    timeout_field = "wait_ready_s"
    sensitive_args = {"command": "command", "path": "path"}

    def _command(self, args: StartInput, root: Path) -> str:
        if args.command:
            return args.command
        info = detect_project(root)
        if not info.dev_command:
            raise ToolError("no dev/start script detected; pass command", "NoCommand")
        return " ".join(f'"{c}"' if " " in c else c for c in info.dev_command)

    def assess(self, args: StartInput, ctx: ToolContext) -> RiskAssessment:
        root = Path(ctx.services.path_guard.check(args.path, PathOp.EXECUTE).canonical)
        try:
            cmd = self._command(args, root)
        except ToolError:
            return RiskAssessment(RiskLevel.MEDIUM)
        cls = ctx.services.command_guard.classify(cmd, "powershell", cwd=str(root))
        a = RiskAssessment(max(cls.risk, RiskLevel.MEDIUM), list(cls.reasons))
        if cls.denied:
            a.deny("; ".join(cls.reasons))
        a.facts.command = cmd
        return a

    def describe(self, args: StartInput) -> str:
        return f"start dev server `{args.command or 'dev script'}` in {args.path}"

    def progress_line(self, args: StartInput) -> str | None:
        return "Starting the dev server."

    async def run(self, args: StartInput, ctx: ToolContext) -> ToolResult:
        root = check_path(ctx, args.path, PathOp.EXECUTE).path
        cmd = self._command(args, root)
        argv, _tmp = shell_argv(cmd, "powershell")
        mgr = ctx.services.devservers
        ds = await mgr.start(argv, str(root), ready_pattern=args.ready_pattern, url=args.url, task_id=ctx.task_id,
                             label=f"{root.name}: {cmd[:40]}")
        if args.notify_when_ready and ctx.services.notifier is not None:
            import asyncio

            async def notify_later() -> None:
                if await mgr.wait_ready(ds, 900):
                    await ctx.services.notifier.notify("Dev server ready", f"{root.name} is ready at {ds.url or 'its port'}",
                                                       task_id=ctx.task_id)

            if args.wait_ready_s == 0:
                asyncio.create_task(notify_later())
        if args.wait_ready_s == 0:
            return self.ok(f"Started dev server ({ds.id}); I'll report when it's ready", ds.info())
        ready = await mgr.wait_ready(ds, args.wait_ready_s)
        info = ds.info()
        if ready:
            if args.notify_when_ready and ctx.services.notifier is not None:
                await ctx.services.notifier.notify("Dev server ready", f"{root.name} is ready at {ds.url or 'its port'}",
                                                   task_id=ctx.task_id)
            return self.ok(f"Dev server ready at {ds.url}" if ds.url else "Dev server ready", info)
        if ds.exit_code is not None:
            tail = "\n".join(list(ds.lines)[-20:])
            raise ToolError(f"dev server exited with code {ds.exit_code}:\n{tail}", "DevServerCrashed")
        return self.ok(f"Dev server started but not ready after {args.wait_ready_s:.0f}s", info)

    async def verify(self, args: StartInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        ds = ctx.services.devservers.servers.get(result.data["id"])
        if ds is None:
            return VerificationResult(verified=False, note="server record missing")
        checks = [Check(name="process running", passed=ds.exit_code is None)]
        if args.wait_ready_s > 0:
            checks.append(Check(name="ready line seen", passed=ds.ready_line is not None, detail=(ds.ready_line or "")[:80]))
            if ds.url:
                checks.append(Check(name="HTTP probe answered", passed=ds.http_status is not None, detail=str(ds.http_status)))
        return VerificationResult.from_checks(checks, ds.info())


class IdInput(ToolInput):
    id: str | None = Field(None, description="dev server id; omit to list all")
    lines: int = Field(60, ge=1, le=2000)


class DevServerStatus(Tool):
    name = "devserver.status"
    description = "Status of managed dev servers (starting/ready/crashed), with recent log lines."
    input_model = IdInput
    capabilities = ("process.read",)
    categories = ("dev",)
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: IdInput, ctx: ToolContext) -> ToolResult:
        mgr = ctx.services.devservers
        servers = [mgr.servers[args.id]] if args.id and args.id in mgr.servers else list(mgr.servers.values())
        if args.id and args.id not in mgr.servers:
            raise ToolError(f"unknown dev server {args.id}", "NotFound")
        infos = []
        view = []
        for ds in servers:
            info = ds.info()
            info["log"] = list(ds.lines)[-args.lines:]
            infos.append(info)
            view.append(f"{ds.id} [{ds.status()}] {ds.label} {ds.url or ''}\n" + "\n".join(info["log"][-15:]))
        return self.ok(f"{len(infos)} dev server(s)", {"servers": infos}, model_view="\n\n".join(view) or "none running")


class StopInput(ToolInput):
    id: str


class DevServerStop(Tool):
    name = "devserver.stop"
    description = "Stop a managed dev server (kills its process tree)."
    input_model = StopInput
    capabilities = ("process.kill",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev",)

    async def run(self, args: StopInput, ctx: ToolContext) -> ToolResult:
        try:
            ds = await ctx.services.devservers.stop(args.id)
        except KeyError as exc:
            raise ToolError(f"unknown dev server {args.id}", "NotFound") from exc
        return self.ok(f"Stopped {ds.label}", ds.info())

    async def verify(self, args: StopInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        ds = ctx.services.devservers.servers.get(args.id)
        return VerificationResult.from_checks([Check(name="process exited", passed=ds is not None and ds.exit_code is not None)])


TOOLS: list[type[Tool]] = [DevServerStart, DevServerStatus, DevServerStop]
