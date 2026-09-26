"""Terminal tools: free-form shell commands (always classified by the command guard) and argv execution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, ToolStatus, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path
from scar.tools.terminal.runner import ProcessOutcome, run_process, shell_argv


def _classification_to_assessment(cls: Any, command: str) -> RiskAssessment:
    a = RiskAssessment(cls.risk, list(cls.reasons))
    if cls.denied:
        from scar.security.policy import deny_key_for

        a.deny("; ".join(cls.reasons) or "denied by command guard", deny_key_for(cls.reasons))
    a.facts.command = command.strip()
    return a


def _render(o: ProcessOutcome, label: str) -> tuple[str, dict[str, Any], str]:
    status = ("timed out" if o.timed_out else "cancelled" if o.cancelled else "stopped (idle)" if o.idle_killed
              else f"exit code {o.exit_code}")
    summary = f"{label} finished: {status} in {o.duration_s:.1f}s"
    data = {"argv": [*o.argv[:1], "…"] if "-EncodedCommand" in o.argv else o.argv, "cwd": o.cwd, "exit_code": o.exit_code,
            "duration_s": o.duration_s, "stdout": o.stdout, "stderr": o.stderr, "stdout_bytes": o.stdout_bytes,
            "stderr_bytes": o.stderr_bytes, "timed_out": o.timed_out, "cancelled": o.cancelled,
            "idle_killed": o.idle_killed, "stdout_ref": o.stdout_ref, "stderr_ref": o.stderr_ref, "notes": o.notes}
    view = [f"$ {label}", f"[cwd {o.cwd}] [{status}] [{o.duration_s:.1f}s]"]
    if o.stdout.strip():
        view.append("--- stdout ---\n" + o.stdout.rstrip())
    if o.stderr.strip():
        view.append("--- stderr ---\n" + o.stderr.rstrip())
    for ref in (o.stdout_ref, o.stderr_ref):
        if ref:
            view.append(f"[full output: {ref}]")
    view += [f"[{n}]" for n in o.notes]
    return summary, data, "\n".join(view)


class RunInput(ToolInput):
    command: str = Field(description="Command line to run")
    shell: Literal["powershell", "pwsh", "cmd"] = "powershell"
    cwd: str | None = Field(None, description="Working directory")
    timeout_s: float | None = Field(None, gt=0, description="Timeout in seconds (default 120, capped)")
    env: dict[str, str] = Field(default_factory=dict, description="Extra non-secret environment variables")
    expect_exit_code: int | None = None
    expect_output_regex: str | None = None


class TerminalRun(Tool):
    name = "terminal.run"
    description = ("Run a PowerShell (default), pwsh or cmd command and capture exit code, stdout and stderr. "
                   "Non-interactive: programs that wait for input are stopped. Every command is risk-classified.")
    input_model = RunInput
    capabilities = ("terminal.exec",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    timeout = 1900.0
    timeout_field = "timeout_s"
    requires = Requires(setting="terminal_enabled")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    categories = ("terminal", "dev", "system")
    sensitive_args = {"command": "command", "cwd": "path"}
    resource_slot = "subprocess"

    def assess(self, args: RunInput, ctx: ToolContext) -> RiskAssessment:
        cwd = args.cwd or ctx.services.extras.get("cwd")
        cls = ctx.services.command_guard.classify(args.command, args.shell, cwd=cwd)
        a = _classification_to_assessment(cls, args.command)
        if args.cwd:
            chk = ctx.services.path_guard.check(args.cwd, PathOp.EXECUTE)
            if chk.denied:
                a.deny("working directory is a protected location", "secret_paths")
            a.facts.paths.append(chk.canonical)
        return a

    def describe(self, args: RunInput) -> str:
        return f"run `{args.command}` in {args.shell}" + (f" (in {args.cwd})" if args.cwd else "")

    def approval_details(self, args: RunInput) -> dict[str, Any]:
        return {"command": args.command, "shell": args.shell, "cwd": args.cwd or "(current)", "timeout_s": args.timeout_s}

    def progress_line(self, args: RunInput) -> str | None:
        first = args.command.strip().split()[0] if args.command.strip() else ""
        if re.search(r"\b(test|pytest|jest|vitest)\b", args.command):
            return "Running tests."
        if re.search(r"\b(build|compile|tsc|msbuild)\b", args.command):
            return "Building."
        return f"Running {first}." if first else None

    async def run(self, args: RunInput, ctx: ToolContext) -> ToolResult:
        cwd = str(check_path(ctx, args.cwd, PathOp.EXECUTE).path) if args.cwd else ctx.services.extras.get("cwd")
        settings = ctx.services.settings
        timeout = min(args.timeout_s or settings.command_timeout, settings.command_timeout_max)
        argv, tmp = shell_argv(args.command, args.shell)
        try:
            outcome = await run_process(argv, services=ctx.services, cancel=ctx.cancel, cwd=cwd, env=args.env,
                                        timeout=timeout, idle_timeout=min(90.0, timeout), task_id=ctx.task_id,
                                        output_cap=settings.output_cap_bytes, stream_label=args.command.split()[0][:30])
        finally:
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)
        summary, data, view = _render(outcome, args.command)
        data["risk"] = ctx.services.command_guard.classify(args.command, args.shell, cwd=cwd).risk.name
        result = self.ok(summary, data, model_view=view, source=f"terminal:{args.command[:80]}")
        if outcome.timed_out:
            result.status = ToolStatus.TIMEOUT
            result.error_type = "Timeout"
        elif outcome.cancelled:
            result.status = ToolStatus.CANCELLED
        elif outcome.idle_killed:
            result.status = ToolStatus.ERROR
            result.error_type = "WaitingForInput"
        return result

    async def verify(self, args: RunInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        checks: list[Check] = []
        if args.expect_exit_code is not None:
            checks.append(Check(name=f"exit code {args.expect_exit_code}", passed=result.data.get("exit_code") == args.expect_exit_code,
                                detail=str(result.data.get("exit_code"))))
        if args.expect_output_regex:
            text = str(result.data.get("stdout", "")) + str(result.data.get("stderr", ""))
            checks.append(Check(name="output matches", passed=bool(re.search(args.expect_output_regex, text))))
        if not checks:
            return VerificationResult(verified=None, note=f"command exited with {result.data.get('exit_code')}",
                                      evidence={"exit_code": result.data.get("exit_code")})
        return VerificationResult.from_checks(checks, {"exit_code": result.data.get("exit_code")})


class ExecInput(ToolInput):
    argv: list[str] = Field(min_length=1, description="Program and arguments as a list (no shell)")
    cwd: str | None = None
    timeout_s: float | None = Field(None, gt=0)
    env: dict[str, str] = Field(default_factory=dict)
    stdin_text: str | None = None
    expect_exit_code: int | None = None


class TerminalExec(Tool):
    name = "terminal.exec"
    description = "Run a program directly with an argument list (no shell parsing). Preferred for structured calls."
    input_model = ExecInput
    capabilities = ("terminal.exec",)
    side_effects = SideEffect.LOCAL
    timeout = 1900.0
    timeout_field = "timeout_s"
    requires = Requires(setting="terminal_enabled")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    categories = ("terminal", "dev")
    sensitive_args = {"argv": "command", "cwd": "path"}
    resource_slot = "subprocess"

    def assess(self, args: ExecInput, ctx: ToolContext) -> RiskAssessment:
        cls = ctx.services.command_guard.classify_argv(args.argv, cwd=args.cwd or ctx.services.extras.get("cwd"))
        a = _classification_to_assessment(cls, " ".join(args.argv))
        if args.cwd:
            chk = ctx.services.path_guard.check(args.cwd, PathOp.EXECUTE)
            if chk.denied:
                a.deny("working directory is a protected location", "secret_paths")
            a.facts.paths.append(chk.canonical)
        return a

    def describe(self, args: ExecInput) -> str:
        return f"run {' '.join(args.argv)}" + (f" (in {args.cwd})" if args.cwd else "")

    async def run(self, args: ExecInput, ctx: ToolContext) -> ToolResult:
        cwd = str(check_path(ctx, args.cwd, PathOp.EXECUTE).path) if args.cwd else ctx.services.extras.get("cwd")
        settings = ctx.services.settings
        timeout = min(args.timeout_s or settings.command_timeout, settings.command_timeout_max)
        outcome = await run_process(args.argv, services=ctx.services, cancel=ctx.cancel, cwd=cwd, env=args.env,
                                    timeout=timeout, idle_timeout=min(90.0, timeout), task_id=ctx.task_id,
                                    output_cap=settings.output_cap_bytes, stdin_text=args.stdin_text)
        summary, data, view = _render(outcome, " ".join(args.argv))
        result = self.ok(summary, data, model_view=view, source=f"exec:{args.argv[0]}")
        if outcome.timed_out:
            result.status, result.error_type = ToolStatus.TIMEOUT, "Timeout"
        elif outcome.cancelled:
            result.status = ToolStatus.CANCELLED
        elif outcome.idle_killed:
            result.status, result.error_type = ToolStatus.ERROR, "WaitingForInput"
        return result

    async def verify(self, args: ExecInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        if args.expect_exit_code is None:
            return VerificationResult(verified=None, note=f"exit code {result.data.get('exit_code')}")
        return VerificationResult.from_checks([Check(name=f"exit code {args.expect_exit_code}",
                                                     passed=result.data.get("exit_code") == args.expect_exit_code)])


TOOLS: list[type[Tool]] = [TerminalRun, TerminalExec]
