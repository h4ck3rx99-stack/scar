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


_PLAN = reply(content=json.dumps({"steps": [{"intent": "run the tests", "candidate_tools": ["dev.run_tests"]},
                                            {"intent": "fix the code", "candidate_tools": ["fs.edit"]},
                                            {"intent": "run the tests again", "candidate_tools": ["dev.run_tests"]}],
                                  "completion_criteria": "tests pass"}))


def _buggy_project(root: Path) -> Path:
    proj = root / "calcproj"
    (proj / "tests").mkdir(parents=True)
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (proj / "tests" / "__init__.py").write_text("")
    (proj / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    (proj / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    return proj


async def test_diagnosis_without_the_fix_is_not_success(tm, sandbox: Path) -> None:
    """Asked to fix a failing test, a model that only explains the bug must not produce 'succeeded'."""
    proj = _buggy_project(sandbox)
    explain = "The add function subtracts instead of adding, so test_add gets -1 instead of 5."
    script(tm.s, [reply(call("dev.run_tests", path=str(proj))),
                  reply(content="The bug is in add(). I’ll fix it now."),
                  reply(content=explain), reply(content=explain), reply(content=explain), reply(content=explain)])
    task = await tm.run(f"fix the failing test in {proj}")
    assert task.status == TaskStatus.FAILED, task.result_summary
    assert "requested change was made" in task.result_summary
    assert (proj / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"


async def test_narrated_step_is_nudged_until_the_fix_is_done(tm, sandbox: Path) -> None:
    proj = _buggy_project(sandbox)
    c = script(tm.s, [_PLAN, reply(call("dev.run_tests", path=str(proj))),
                      reply(content="add() subtracts. I'll change it to return a + b."),
                      reply(call("fs.edit", path=str(proj / "calc.py"), old="return a - b", new="return a + b")),
                      reply(call("dev.run_tests", path=str(proj))),
                      reply(call("finish", summary="Fixed add() in calc.py; all tests pass."))])
    task = await tm.run(f"fix the failing test in {proj} and run the tests again")
    assert task.status == TaskStatus.SUCCEEDED and task.verification.verified is True, task.result_summary
    assert "return a + b" in (proj / "calc.py").read_text()
    notes = [m.content for _, req in c.requests for m in req.messages if "did not do it" in (m.content or "")]
    assert notes, "the narrated step was nudged"


async def test_fix_without_rerunning_tests_is_not_verified(tm, sandbox: Path) -> None:
    proj = _buggy_project(sandbox)
    script(tm.s, [_PLAN, reply(call("dev.run_tests", path=str(proj))),
                  reply(call("fs.edit", path=str(proj / "calc.py"), old="return a - b", new="return a * b")),
                  reply(call("finish", summary="Fixed it.")),
                  reply(call("finish", summary="Fixed it.")),
                  reply(call("finish", summary="Fixed it."))])
    task = await tm.run(f"fix the failing test in {proj} and run the tests again")
    assert task.status == TaskStatus.FAILED
    assert "tests pass after the change" in task.result_summary


async def test_model_lost_mid_task_reports_partial_work_honestly(tm, sandbox: Path) -> None:
    """The model disappears after an action ran: the task fails, and says so, instead of claiming success."""
    f = sandbox / "half.txt"
    script(tm.s, [_PLAN, reply(call("fs.write", path=str(f), content="part one")),
                  perr(ProviderErrorKind.TRANSIENT), perr(ProviderErrorKind.TRANSIENT), perr(ProviderErrorKind.TRANSIENT),
                  perr(ProviderErrorKind.TRANSIENT), perr(ProviderErrorKind.TRANSIENT), perr(ProviderErrorKind.TRANSIENT)])
    task = await tm.run(f"write half.txt in {sandbox} with part one, then summarise my week")
    assert task.status == TaskStatus.FAILED, task.result_summary
    assert "done" not in task.result_summary.lower().split()
    assert f.exists()  # what already happened is real; the summary must not pretend the rest happened
    assert "partway through" in task.result_summary and "half.txt" in task.result_summary


async def test_state_question_answered_from_memory_is_sent_back_to_check(tm, sandbox: Path) -> None:
    """Asked about the current state, a model repeating an earlier answer without looking is made to check."""
    c = script(tm.s, [reply(content="You have no events today."),
                      reply(call("calendar.list")),
                      reply(content="No events today in the local calendar.")])
    task = await tm.run("what's on my calendar today?")
    assert [o.tool for o in task.observations] == ["calendar.list"]
    assert "not connected" in task.result_summary  # the local-calendar caveat reaches the user
    assert any("current state" in (m.content or "") for _, req in c.requests for m in req.messages)
