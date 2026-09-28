"""Terminal tools: timeouts, caps, cancellation, process-tree kill, command guard integration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from scar.core.types import RiskLevel, ToolStatus

pytestmark = pytest.mark.windows


@pytest.fixture
async def parts(runtime_parts):  # type: ignore[no-untyped-def]
    from scar.runtime.registration import build_registry

    registry = build_registry(runtime_parts["services"], ["scar.tools.internal", "scar.tools.terminal.tools"])
    from scar.tools.pipeline import ToolPipeline

    return {"services": runtime_parts["services"], "pipeline": ToolPipeline(runtime_parts["services"], registry)}


async def test_powershell_output_and_exit_code(parts, ctx_factory, sandbox: Path) -> None:
    obs = await parts["pipeline"].execute("terminal.run", {"command": "Write-Output 'hi there'; exit 3"}, ctx_factory("run it"))
    assert obs.result.ok, obs.result.summary
    assert obs.result.data["exit_code"] == 3
    assert "hi there" in obs.result.data["stdout"]
    assert "UNTRUSTED_DATA" in obs.result.model_view


async def test_quoted_program_path_runs_in_powershell(parts, ctx_factory, sandbox: Path) -> None:
    """Recorded 2026-09-28 (acceptance D3.11): a package.json script `"C:/…/python.exe" server.py` run as-is in PowerShell
    was a parse error, so the dev server never started. Quoted program paths get the call operator."""
    py = sys.executable.replace("\\", "/")
    obs = await parts["pipeline"].execute("terminal.run", {"command": f'"{py}" --version'}, ctx_factory("run it"))
    assert obs.result.ok, obs.result.summary
    assert obs.result.data["exit_code"] == 0
    assert "Python 3" in obs.result.data["stdout"] + obs.result.data["stderr"]


@pytest.mark.parametrize(("command", "expected"), [
    ('"C:/Program Files/nodejs/npm.cmd" run dev', '& "C:/Program Files/nodejs/npm.cmd" run dev'),
    ("'C:\\Tools\\app.exe' --flag", "& 'C:\\Tools\\app.exe' --flag"),
    ('"C:\\Tools\\app.exe"', '& "C:\\Tools\\app.exe"'),
    ('"hello world"', '"hello world"'),              # a plain string stays a string
    ("Write-Output 'x'", "Write-Output 'x'"),
    ('& "C:/x/app.exe" a', '& "C:/x/app.exe" a'),     # already a call
    ('"./run.ps1" -Fast', '& "./run.ps1" -Fast'),
])
def test_powershell_call_form(command: str, expected: str) -> None:
    from scar.security.command_guard import powershell_call_form

    assert powershell_call_form(command) == expected


async def test_cmd_shell_with_quotes(parts, ctx_factory, sandbox: Path) -> None:
    obs = await parts["pipeline"].execute("terminal.run", {"command": 'echo "quoted & text"', "shell": "cmd"}, ctx_factory("run it"))
    assert obs.result.ok
    assert "quoted & text" in obs.result.data["stdout"]


async def test_timeout_kills_tree(parts, ctx_factory, sandbox: Path) -> None:
    cmd = "Start-Process -NoNewWindow ping -ArgumentList '-n','30','127.0.0.1'; Start-Sleep 30"
    before = {p.pid for p in psutil.process_iter()}
    obs = await parts["pipeline"].execute("terminal.run", {"command": cmd, "timeout_s": 3}, ctx_factory("run it"))
    assert obs.result.status == ToolStatus.TIMEOUT
    await asyncio.sleep(1.0)
    leftovers = [p for p in psutil.process_iter(["name"]) if p.pid not in before and (p.info["name"] or "").lower() == "ping.exe"]
    assert not leftovers, "ping grandchild survived the timeout"


async def test_output_cap_spills_to_artifact(parts, ctx_factory, sandbox: Path) -> None:
    services = parts["services"]
    services.settings.output_cap_bytes = 2000
    obs = await parts["pipeline"].execute("terminal.exec", {"argv": [sys.executable, "-c", "print('x'*50000)"]},
                                          ctx_factory("run it"))
    assert obs.result.ok
    assert obs.result.data["stdout_bytes"] > 50000
    ref = obs.result.data["stdout_ref"]
    assert ref and ref.startswith("artifact://")
    assert services.artifacts.resolve(ref).stat().st_size >= 50000
    assert len(obs.result.data["stdout"]) < 5000


async def test_cancellation_kills_process(parts, ctx_factory, sandbox: Path) -> None:
    ctx = ctx_factory("run it")
    task = asyncio.create_task(parts["pipeline"].execute(
        "terminal.exec", {"argv": [sys.executable, "-c", "import time; time.sleep(60)"], "timeout_s": 60}, ctx))
    await asyncio.sleep(2.0)
    ctx.cancel.cancel("user pressed stop")
    obs = await asyncio.wait_for(task, 20)
    assert obs.result.status == ToolStatus.CANCELLED
    assert not parts["services"].processes.list()


async def test_waiting_for_input_is_reported(parts, ctx_factory, sandbox: Path) -> None:
    # stdin is closed, so input() raises EOFError immediately instead of hanging
    obs = await parts["pipeline"].execute("terminal.exec", {"argv": [sys.executable, "-c", "input('name? ')"]}, ctx_factory("run it"))
    assert obs.result.ok
    assert obs.result.data["exit_code"] != 0
    assert "EOFError" in obs.result.data["stderr"]


async def test_dangerous_command_denied_or_asks(parts, ctx_factory, sandbox: Path) -> None:
    pipeline = parts["pipeline"]
    obs = await pipeline.execute("terminal.run", {"command": "Set-MpPreference -DisableRealtimeMonitoring $true"}, ctx_factory("x"))
    assert obs.result.status == ToolStatus.DENIED
    ctx = ctx_factory("clean up", autonomy=4)
    obs = await pipeline.execute("terminal.run", {"command": f"Remove-Item -Recurse -Force {sandbox}"}, ctx)
    assert obs.result.status == ToolStatus.DENIED  # CRITICAL -> ask -> no channel -> deny
    assert ctx.task.action_history[-1].risk == RiskLevel.CRITICAL
    assert sandbox.exists()


async def test_secrets_not_passed_to_children(parts, ctx_factory, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_testsecretvalue1234567890abcdef")
    obs = await parts["pipeline"].execute("terminal.exec", {"argv": [sys.executable, "-c",
                                          "import os; print(os.environ.get('GROQ_API_KEY', 'absent'))"]}, ctx_factory("x"))
    assert "absent" in obs.result.data["stdout"]


async def test_expect_exit_code_postcondition(parts, ctx_factory, sandbox: Path) -> None:
    obs = await parts["pipeline"].execute("terminal.exec", {"argv": [sys.executable, "-c", "raise SystemExit(1)"],
                                                            "expect_exit_code": 0}, ctx_factory("x"))
    assert obs.result.status == ToolStatus.ERROR
    assert obs.result.error_type == "VerificationFailed"
