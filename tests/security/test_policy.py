"""Policy engine, autonomy mapping, grants, approvals and TOCTOU re-approval (C4.2-C4.6, D2, D5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scar.core.types import PolicyDecision, RiskLevel, SideEffect, ToolStatus
from scar.security.approval import ApprovalError, ApprovalResponse
from scar.security.grants import Grant, GrantKind, GrantScope, UserAuthority
from scar.security.policy import PolicyEngine, PolicyInput, _parse_rule
from scar.security.risk import ActionFacts, RiskAssessment


def inp(risk: RiskLevel, autonomy: int = 3, **kw) -> PolicyInput:  # type: ignore[no-untyped-def]
    a = RiskAssessment(risk)
    for k in ("floor", "deny_key"):
        if k in kw:
            setattr(a, k, kw.pop(k))
    if "facts" in kw:
        a.facts = kw.pop("facts")
    return PolicyInput(tool=kw.pop("tool", "fs.write"), capabilities=kw.pop("caps", ["fs.write"]), args_hash=kw.pop("h", "h1"),
                       assessment=a, side_effects=kw.pop("side", SideEffect.LOCAL), autonomy_level=autonomy,
                       task_id=kw.pop("task_id", "t1"), **kw)


@pytest.fixture
def engine(services) -> PolicyEngine:  # type: ignore[no-untyped-def]
    return services.policy


EXPECTED = {
    (0, RiskLevel.LOW): PolicyDecision.DENY, (1, RiskLevel.LOW): PolicyDecision.DENY,
    (2, RiskLevel.LOW): PolicyDecision.ALLOW, (2, RiskLevel.MEDIUM): PolicyDecision.ASK, (2, RiskLevel.HIGH): PolicyDecision.ASK,
    (3, RiskLevel.LOW): PolicyDecision.ALLOW, (3, RiskLevel.MEDIUM): PolicyDecision.ALLOW, (3, RiskLevel.HIGH): PolicyDecision.ASK,
    (4, RiskLevel.MEDIUM): PolicyDecision.ALLOW, (4, RiskLevel.HIGH): PolicyDecision.ASK,
}


@pytest.mark.parametrize(("key", "want"), list(EXPECTED.items()))
def test_autonomy_matrix(engine: PolicyEngine, key, want) -> None:  # type: ignore[no-untyped-def]
    level, risk = key
    assert engine.decide(inp(risk, level)).decision == want


@settings(max_examples=100, deadline=None)
@given(st.integers(0, 4), st.booleans(), st.sampled_from(list(GrantScope)))
def test_critical_never_auto_allowed(level: int, with_grant: bool, scope: GrantScope) -> None:
    from scar.security.grants import GrantStore
    from scar.storage.db import Database
    import tempfile

    db = Database(Path(tempfile.mkdtemp()) / "p.db")
    try:
        gs = GrantStore(db, "s1")
        if with_grant:
            gs.create(Grant(kind=GrantKind.TOOL, tool="*", scope=scope, max_risk=RiskLevel.CRITICAL, task_id="t1",
                            expires_at=None), UserAuthority("cli"))
        pe = PolicyEngine.load(gs)
        out = pe.decide(inp(RiskLevel.CRITICAL, level))
        assert out.decision != PolicyDecision.ALLOW
        if level >= 2:
            assert out.critical_confirmation and not out.grantable
    finally:
        db.close()


def test_hard_deny_floor_and_manual_override(engine: PolicyEngine) -> None:
    out = engine.decide(inp(RiskLevel.CRITICAL, 4, floor=PolicyDecision.DENY, deny_key="disable_defender"))
    assert out.decision == PolicyDecision.DENY
    engine.hard_deny["disable_defender"] = False  # only possible by editing the policy file by hand
    out = engine.decide(inp(RiskLevel.CRITICAL, 4, floor=PolicyDecision.DENY, deny_key="disable_defender"))
    assert out.decision == PolicyDecision.ASK and out.critical_confirmation


def test_deny_rule_beats_allow_rule(engine: PolicyEngine) -> None:
    engine.rules.insert(0, _parse_rule({"id": "allow-all", "effect": "allow", "match": {"tool": ["fs.*"]}}, "test"))
    engine.rules.insert(0, _parse_rule({"id": "deny-write", "effect": "deny", "match": {"tool": ["fs.write"]}}, "test"))
    assert engine.decide(inp(RiskLevel.LOW, 3)).decision == PolicyDecision.DENY


def test_escalation_rule(engine: PolicyEngine) -> None:
    out = engine.decide(inp(RiskLevel.MEDIUM, 4, tool="browser.click", caps=["finance.purchase"]))
    assert out.risk == RiskLevel.CRITICAL and out.decision == PolicyDecision.ASK


def test_tainted_and_out_of_scope_ask_even_with_grant(services, engine: PolicyEngine) -> None:  # type: ignore[no-untyped-def]
    services.grants.create(Grant(kind=GrantKind.TOOL, tool="email.send", scope=GrantScope.SESSION, max_risk=RiskLevel.HIGH),
                           UserAuthority("cli"))
    out = engine.decide(inp(RiskLevel.HIGH, 4, tool="email.send", caps=["comms.send.email"], tainted=["recipient x from web"]))
    assert out.decision == PolicyDecision.ASK and not out.grantable
    out = engine.decide(inp(RiskLevel.HIGH, 4, tool="email.send", caps=["comms.send.email"], out_of_scope=["not named"]))
    assert out.decision == PolicyDecision.ASK and not out.grantable
    out = engine.decide(inp(RiskLevel.HIGH, 3, tool="email.send", caps=["comms.send.email"]))
    assert out.decision == PolicyDecision.ALLOW and out.grant is not None


def test_grant_scopes(services) -> None:  # type: ignore[no-untyped-def]
    gs = services.grants
    facts = ActionFacts(command="npm test")
    g = gs.create(Grant(kind=GrantKind.COMMAND, tool="terminal.run", match={"command_prefix": "npm test"},
                        scope=GrantScope.PERSISTENT, max_risk=RiskLevel.MEDIUM), UserAuthority("cli"))
    assert gs.find("terminal.run", "x", RiskLevel.MEDIUM, facts, "t")[1] is not None
    # chaining after an allowed prefix never matches
    assert gs.find("terminal.run", "x", RiskLevel.MEDIUM, ActionFacts(command="npm test; rm -rf ~"), "t")[1] is None
    assert gs.find("terminal.run", "x", RiskLevel.HIGH, facts, "t")[1] is None  # above max_risk
    gs.revoke(g.grant_id, UserAuthority("cli"))
    assert gs.find("terminal.run", "x", RiskLevel.MEDIUM, facts, "t")[1] is None
    once = gs.create(Grant(kind=GrantKind.EXACT, tool="fs.write", args_hash="h9", scope=GrantScope.ONCE, task_id="t"),
                     UserAuthority("cli"))
    found = gs.find("fs.write", "h9", RiskLevel.MEDIUM, ActionFacts(), "t")[1]
    assert found is not None
    gs.consume(found)
    assert gs.find("fs.write", "h9", RiskLevel.MEDIUM, ActionFacts(), "t")[1] is None
    assert once.uses == 1


def test_only_user_authority_creates_grants(services) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PermissionError):
        services.grants.create(Grant(kind=GrantKind.TOOL, tool="*", scope=GrantScope.SESSION), object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        UserAuthority("model")


def test_tools_cannot_reach_grant_api() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "scar" / "tools"
    offenders = [p for p in root.rglob("*.py") if "UserAuthority" in p.read_text(encoding="utf-8")
                 or "grants.create" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


async def _pending(services):  # type: ignore[no-untyped-def]
    for _ in range(200):
        await asyncio.sleep(0.01)
        if services.approvals.pending():
            return services.approvals.pending()[0]
    raise AssertionError("no approval requested")


async def test_approval_allow_session_creates_grant_and_reuses(runtime_parts, ctx_factory, sandbox: Path) -> None:
    s = runtime_parts["services"]
    p = runtime_parts["pipeline"]
    s.approvals.attach_channel("cli")
    (sandbox / "a.txt").write_text("x")
    (sandbox / "b.txt").write_text("x")
    ctx = ctx_factory("delete a.txt and b.txt")
    t = asyncio.create_task(p.execute("fs.delete", {"paths": [str(sandbox / "a.txt")]}, ctx))
    req = await _pending(s)
    s.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_SESSION, "cli")
    assert (await t).result.ok
    obs = await p.execute("fs.delete", {"paths": [str(sandbox / "b.txt")]}, ctx)
    assert obs.result.ok and ctx.task.action_history[-1].decision_reason.startswith("allowed by your session")
    rows = s.audit.recent(20)
    assert any(r["kind"] == "approval_resolved" for r in rows)


async def test_toctou_changed_args_require_reapproval(runtime_parts, ctx_factory, sandbox: Path) -> None:
    from scar.security.approval import ApprovalResolution

    s = runtime_parts["services"]
    p = runtime_parts["pipeline"]
    (sandbox / "victim.txt").write_text("x")
    (sandbox / "approved.txt").write_text("x")
    forged = ApprovalResolution(request_id="apr_x", response=ApprovalResponse.ALLOW_ONCE, channel="cli", args_hash="0" * 64)
    ctx = ctx_factory("delete approved.txt")
    obs = await p.execute("fs.delete", {"paths": [str(sandbox / "victim.txt")]}, ctx, approved=forged)
    assert obs.result.status == ToolStatus.DENIED  # the approval was for different arguments: re-ask → no channel → deny
    assert (sandbox / "victim.txt").exists()


async def test_timeout_means_deny(runtime_parts, ctx_factory, sandbox: Path) -> None:
    s = runtime_parts["services"]
    s.settings.approval_timeout = 0.3
    s.approvals.attach_channel("cli")
    (sandbox / "t.txt").write_text("x")
    obs = await runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(sandbox / "t.txt")]}, ctx_factory("delete t.txt"))
    assert obs.result.status == ToolStatus.DENIED and "timed out" in obs.result.summary


async def test_voice_cannot_confirm_critical_or_persist_without_readback(runtime_parts, ctx_factory, sandbox: Path) -> None:
    s = runtime_parts["services"]
    s.approvals.attach_channel("voice")
    s.approvals.attach_channel("cli")
    (sandbox / "c.txt").write_text("x")
    t = asyncio.create_task(runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(sandbox / "c.txt")], "permanent": True},
                                                              ctx_factory("permanently delete c.txt")))
    req = await _pending(s)
    with pytest.raises(ApprovalError):
        s.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ONCE, "voice", typed_confirmation=req.confirmation_code)
    s.approvals.resolve(req.request_id, ApprovalResponse.DENY, "voice")
    assert (await t).result.status == ToolStatus.DENIED
    (sandbox / "d.txt").write_text("x")
    t = asyncio.create_task(runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(sandbox / "d.txt")]}, ctx_factory("delete d.txt")))
    req = await _pending(s)
    with pytest.raises(ApprovalError):
        s.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ALWAYS, "voice")
    s.approvals.resolve(req.request_id, ApprovalResponse.ALLOW_ALWAYS, "voice", voice_readback_confirmed=True)
    assert (await t).result.ok


async def test_critical_only_keyboard_channel_denied_without_one(runtime_parts, ctx_factory, sandbox: Path) -> None:
    s = runtime_parts["services"]
    s.approvals.attach_channel("voice")
    (sandbox / "e.txt").write_text("x")
    obs = await runtime_parts["pipeline"].execute("fs.delete", {"paths": [str(sandbox / "e.txt")], "permanent": True},
                                                  ctx_factory("permanently delete e.txt", autonomy=4))
    assert obs.result.status == ToolStatus.DENIED and "keyboard" in obs.result.summary
