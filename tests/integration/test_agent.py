"""Agent runtime with a scripted model: states, persistence, planning, verification, loop prevention, cancellation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scar.agent.runner import TaskManager
from scar.core.types import TaskStatus
from scar.providers.errors import ProviderErrorKind
from tests.helpers import ScriptedChatClient, call, install_scripted_router, perr, reply


@pytest.fixture
def tm(runtime_parts):  # type: ignore[no-untyped-def]
    services = runtime_parts["services"]
    m = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    services.tasks = m
    return m


def script(services, steps):  # type: ignore[no-untyped-def]
    c = ScriptedChatClient("s", steps)
    install_scripted_router(services, {"s": c})
    return c


async def test_success_with_verified_evidence_and_persistence(tm, sandbox: Path) -> None:
    services = tm.s
    f = sandbox / "out.txt"
    events = []
    services.bus.add_listener(lambda e: events.append(e.kind))
    script(services, [reply(call("fs.write", path=str(f), content="hi")),
                      reply(call("finish", summary="Created out.txt.", evidence=[{"kind": "file", "detail": {"path": str(f)}}]))])
    task = await tm.run(f"create out.txt in {sandbox} containing hi")
    assert task.status == TaskStatus.SUCCEEDED and task.verification.verified is True
    assert task.result_summary == "Created out.txt."
    row = services.db.query_one("SELECT status, result_summary FROM tasks WHERE task_id = ?", (task.task_id,))
    assert row["status"] == "succeeded"
    assert events[0] == "task_started" and "task_completed" in events and "tool_completed" in events


async def test_false_claim_is_caught(tm, sandbox: Path) -> None:
    script(tm.s, [reply(call("finish", summary="Created ghost.txt.", evidence=[{"kind": "file", "detail": {"path": str(sandbox / "ghost.txt")}}])),
                  reply(call("finish", summary="Created ghost.txt.", evidence=[{"kind": "file", "detail": {"path": str(sandbox / "ghost.txt")}}]))])
    task = await tm.run(f"create ghost.txt in {sandbox}")
    assert task.status == TaskStatus.FAILED
    assert "verification failed" in task.result_summary.lower()


async def test_unverifiable_is_reported_honestly(tm, sandbox: Path) -> None:
    script(tm.s, [reply(call("terminal.exec", argv=["cmd", "/c", "echo", "hi"])),
                  reply(call("finish", summary="Ran it."))])
    task = await tm.run("run echo hi")
    assert task.status == TaskStatus.SUCCEEDED
    assert "couldn't verify" in task.result_summary.lower() or "not independently confirmed" in task.result_summary.lower()


async def test_conversation_without_tools(tm) -> None:
    script(tm.s, [reply(content="Hello! How can I help?")])
    task = await tm.run("hello")
    assert task.status == TaskStatus.SUCCEEDED and "Hello" in task.result_summary and not task.action_history


async def test_repeated_failure_triggers_replan_then_abort(tm, sandbox: Path) -> None:
    missing = str(sandbox / "nope.txt")
    bad = [reply(call("fs.read", path=missing)) for _ in range(12)]
    plan_json = reply(content=json.dumps({"steps": [{"intent": "try again"}], "completion_criteria": "x"}))
    script(tm.s, bad[:4] + [plan_json] + bad[4:6] + [plan_json] + bad[6:])
    task = await tm.run(f"read {missing}")
    assert task.status == TaskStatus.FAILED
    assert "kept failing" in task.result_summary or "no progress" in task.result_summary


async def test_step_budget(tm, sandbox: Path) -> None:
    tm.s.settings.max_steps = 3
    script(tm.s, [reply(call("fs.list", path=str(sandbox))) for _ in range(10)])
    task = await tm.run(f"list {sandbox} forever")
    assert task.status == TaskStatus.FAILED and "ran out of steps" in task.result_summary


async def test_cancellation_mid_task(tm) -> None:
    import sys

    script(tm.s, [reply(call("terminal.exec", argv=[sys.executable, "-c", "import time; time.sleep(60)"], timeout_s=60))])
    handle = await tm.submit("run a long script")
    await asyncio.sleep(1.5)
    tm.cancel(handle.task.task_id)
    task = await asyncio.wait_for(handle.future, 20)
    assert task.status == TaskStatus.CANCELLED
    assert not tm.s.processes.list()


async def test_degraded_mode_message(tm) -> None:
    script(tm.s, [perr(ProviderErrorKind.AUTH)])
    task = await tm.run("summarise my week")
    assert task.status == TaskStatus.FAILED and "no language model is reachable" in task.result_summary


async def test_fast_path_works_without_llm(tm, sandbox: Path) -> None:
    script(tm.s, [perr(ProviderErrorKind.AUTH)])
    task = await tm.run("remind me in 10 minutes to stretch")
    assert task.status == TaskStatus.SUCCEEDED and "remind" in task.result_summary.lower()
    assert tm.s.db.query_one("SELECT text FROM schedules")["text"] == "stretch"


async def test_malformed_tool_call_repair(tm, sandbox: Path) -> None:
    from scar.providers.base import ToolCall

    broken = reply(ToolCall(id="c1", name="fs__list", raw_arguments="{oops", parse_error="bad json"))
    c = script(tm.s, [broken, reply(call("fs.list", path=str(sandbox))), reply(call("finish", summary="Listed."))])
    task = await tm.run(f"list {sandbox}")
    assert task.status == TaskStatus.SUCCEEDED
    assert any("invalid JSON" in m.content for _, r in c.requests for m in r.messages if m.role == "tool")


async def test_planning_for_multistep(tm, sandbox: Path) -> None:
    plan = reply(content=json.dumps({"steps": [{"intent": "write a", "candidate_tools": ["fs.write"]},
                                               {"intent": "write b", "candidate_tools": ["fs.write"]}],
                                     "completion_criteria": "both exist"}))
    script(tm.s, [plan, reply(call("fs.write", path=str(sandbox / "a"), content="1")),
                  reply(call("fs.write", path=str(sandbox / "b"), content="2")), reply(call("finish", summary="Both written."))])
    task = await tm.run(f"write file a in {sandbox} and then write file b next to it")
    assert task.plan is not None and len(task.plan.steps) == 2 and all(s.done for s in task.plan.steps)
    assert task.status == TaskStatus.SUCCEEDED


async def test_interrupted_tasks_marked_on_restart(tm) -> None:
    from scar.core.types import TaskState

    t = TaskState(objective="long thing", status=TaskStatus.RUNNING)
    tm.persist(t)
    rows = tm.mark_interrupted()
    assert [r["task_id"] for r in rows] == [t.task_id]
    assert tm.get(t.task_id)["status"] == "interrupted"


async def test_autonomy_1_suggests_only(tm, sandbox: Path) -> None:
    script(tm.s, [reply(content="1. List the folder\n2. Delete old files (risky)")])
    task = await tm.run(f"clean up {sandbox}", autonomy=1)
    assert task.status == TaskStatus.SUCCEEDED and "1." in task.result_summary and not task.action_history


async def test_subagent_delegation(tm, sandbox: Path) -> None:
    script(tm.s, [reply(call("agent.delegate", tasks=[{"role": "tester", "objective": f"list {sandbox}"}])),
                  reply(call("fs.list", path=str(sandbox))), reply(call("finish", summary="child done")),
                  reply(call("finish", summary="Delegated."))])
    task = await tm.run(f"research and code: have a tester look at {sandbox}")
    assert task.status == TaskStatus.SUCCEEDED
    child = tm.s.db.query_one("SELECT role, status FROM tasks WHERE parent_task_id = ?", (task.task_id,))
    assert child["role"] == "tester" and child["status"] == "succeeded"
