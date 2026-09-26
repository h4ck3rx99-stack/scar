"""Filesystem tools through the full pipeline (validation → risk → policy → approval → exec → verify)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from scar.core.types import PolicyDecision, RiskLevel, ToolStatus
from scar.security.approval import ApprovalResponse


async def test_write_read_roundtrip_verified(runtime_parts, ctx_factory, sandbox: Path) -> None:
    pipeline = runtime_parts["pipeline"]
    ctx = ctx_factory("create notes.txt containing hello world")
    target = sandbox / "notes.txt"
    obs = await pipeline.execute("fs.write", {"path": str(target), "content": "hello world\n"}, ctx)
    assert obs.result.status == ToolStatus.OK, obs.result.summary
    assert obs.result.verification is not None and obs.result.verification.verified is True
    assert hashlib.sha256(target.read_bytes()).hexdigest() == obs.result.data["sha256"]
    obs = await pipeline.execute("fs.read", {"path": str(target)}, ctx)
    assert obs.result.ok
    assert "UNTRUSTED_DATA" in obs.result.model_view  # file contents are wrapped as data
    assert obs.result.data["content"] == "hello world\n"


async def test_invalid_args_rejected(runtime_parts, ctx_factory) -> None:
    obs = await runtime_parts["pipeline"].execute("fs.write", {"path": "x"}, ctx_factory())
    assert obs.result.status == ToolStatus.ERROR
    assert obs.result.error_type == "InvalidInput"


async def test_secret_path_denied(runtime_parts, ctx_factory) -> None:
    obs = await runtime_parts["pipeline"].execute("fs.read", {"path": "~/.ssh/id_rsa"}, ctx_factory())
    assert obs.result.status == ToolStatus.DENIED


async def test_delete_requires_approval_and_times_out_to_deny(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services = runtime_parts["services"]
    f = sandbox / "old.txt"
    f.write_text("x")
    services.approvals.attach_channel("cli")
    ctx = ctx_factory("delete old.txt")
    task = asyncio.create_task(runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(f)]}, ctx))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if services.approvals.pending():
            break
    req = services.approvals.pending()[0]
    assert req.risk == RiskLevel.HIGH
    services.approvals.resolve(req.request_id, ApprovalResponse.DENY, "cli")
    obs = await task
    assert obs.result.status == ToolStatus.DENIED
    assert f.exists()


async def test_delete_to_recycle_bin_after_approval(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services = runtime_parts["services"]
    f = sandbox / "trash-me.txt"
    f.write_text("bye")
    services.approvals.attach_channel("cli")
    ctx = ctx_factory("delete trash-me.txt")
    task = asyncio.create_task(runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(f)]}, ctx))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if services.approvals.pending():
            break
    services.approvals.resolve(services.approvals.pending()[0].request_id, ApprovalResponse.ALLOW_ONCE, "cli")
    obs = await task
    assert obs.result.status == ToolStatus.OK, obs.result.summary
    assert obs.result.verification.verified is True
    assert not f.exists()


async def test_permanent_delete_is_critical_and_needs_typed_code(runtime_parts, ctx_factory, sandbox: Path) -> None:
    services = runtime_parts["services"]
    f = sandbox / "perm.txt"
    f.write_text("x")
    services.approvals.attach_channel("cli")
    ctx = ctx_factory("permanently delete perm.txt", autonomy=4)
    task = asyncio.create_task(runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(f)], "permanent": True}, ctx))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if services.approvals.pending():
            break
    req = services.approvals.pending()[0]
    assert req.critical and req.risk == RiskLevel.CRITICAL
    from scar.security.approval import ApprovalError

    with pytest.raises(ApprovalError):
        services.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_SESSION, "cli")
    with pytest.raises(ApprovalError):
        services.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ONCE, "voice", typed_confirmation=req.confirmation_code)
    with pytest.raises(ApprovalError):
        services.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ONCE, "cli", typed_confirmation="WRONG")
    services.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ONCE, "cli", typed_confirmation=req.confirmation_code)
    obs = await task
    assert obs.result.ok and not f.exists()


async def test_no_channel_means_deny(runtime_parts, ctx_factory, sandbox: Path) -> None:
    f = sandbox / "keep.txt"
    f.write_text("x")
    obs = await runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(f)]}, ctx_factory("delete keep.txt"))
    assert obs.result.status == ToolStatus.DENIED
    assert f.exists()


async def test_edit_exact_replacement(runtime_parts, ctx_factory, sandbox: Path) -> None:
    f = sandbox / "code.py"
    f.write_text("def add(a, b):\n    return a - b\n")
    obs = await runtime_parts["pipeline"].execute(
        "fs.edit", {"path": str(f), "old": "return a - b", "new": "return a + b"}, ctx_factory("fix the bug"))
    assert obs.result.ok, obs.result.summary
    assert "a + b" in f.read_text()
    obs = await runtime_parts["pipeline"].execute(
        "fs.edit", {"path": str(f), "old": "nonexistent", "new": "x"}, ctx_factory("fix the bug"))
    assert obs.result.status == ToolStatus.ERROR and obs.result.error_type == "NoMatch"


async def test_search_by_name_and_content(runtime_parts, ctx_factory, sandbox: Path) -> None:
    (sandbox / "a" / "b").mkdir(parents=True)
    (sandbox / "a" / "decoy.txt").write_text("nothing here")
    (sandbox / "a" / "b" / "target-q7.txt").write_text("the secret phrase is zebra-42")
    pipeline = runtime_parts["pipeline"]
    obs = await pipeline.execute("fs.search", {"root": str(sandbox), "name": "q7"}, ctx_factory("find q7"))
    assert [r["path"] for r in obs.result.data["results"]] == [str(sandbox / "a" / "b" / "target-q7.txt")]
    obs = await pipeline.execute("fs.search", {"root": str(sandbox), "content": "zebra-42"}, ctx_factory("find zebra"))
    assert obs.result.data["results"][0]["path"].endswith("target-q7.txt")


async def test_dry_run_does_not_write(runtime_parts, ctx_factory, sandbox: Path) -> None:
    ctx = ctx_factory("create dry.txt", dry_run=True)
    obs = await runtime_parts["pipeline"].execute("fs.write", {"path": str(sandbox / "dry.txt"), "content": "x"}, ctx)
    assert obs.result.ok and obs.result.data.get("dry_run")
    assert not (sandbox / "dry.txt").exists()


async def test_autonomy_levels(runtime_parts, ctx_factory, sandbox: Path) -> None:
    pipeline = runtime_parts["pipeline"]
    for level, expected in [(0, ToolStatus.DENIED), (1, ToolStatus.DENIED)]:
        obs = await pipeline.execute("fs.list", {"path": str(sandbox)}, ctx_factory("list files", autonomy=level))
        assert obs.result.status == expected
    obs = await pipeline.execute("fs.list", {"path": str(sandbox)}, ctx_factory("list files", autonomy=2))
    assert obs.result.ok
    # MEDIUM at level 2 needs approval -> no channel -> denied
    obs = await pipeline.execute("fs.write", {"path": str(sandbox / "l2.txt"), "content": "x"}, ctx_factory("create l2", autonomy=2))
    assert obs.result.status == ToolStatus.DENIED
    obs = await pipeline.execute("fs.write", {"path": str(sandbox / "l3.txt"), "content": "x"}, ctx_factory("create l3", autonomy=3))
    assert obs.result.ok


async def test_decision_recorded(runtime_parts, ctx_factory, sandbox: Path) -> None:
    ctx = ctx_factory("list")
    await runtime_parts["pipeline"].execute("fs.list", {"path": str(sandbox)}, ctx)
    action = ctx.task.action_history[-1]
    assert action.decision == PolicyDecision.ALLOW
    row = runtime_parts["services"].db.query_one("SELECT * FROM tool_calls WHERE action_id = ?", (action.action_id,))
    assert row is not None and row["status"] == "ok"
