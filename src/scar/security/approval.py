"""Approval flow (C4.4, C4.5).

An ``ApprovalRequest`` states exactly what will happen. Interactive channels
(CLI, TUI, voice, IPC clients) present it and call ``resolve``. Approvals are
bound to the request id *and* the args hash: the pipeline re-hashes the
arguments immediately before execution and refuses to run if they changed.
Timeouts and the absence of any interactive channel mean DENY.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from scar.core.events import ApprovalRequested, ApprovalResolved, EventBus
from scar.core.ids import new_id
from scar.core.types import RiskLevel
from scar.security.audit import AuditLog
from scar.security.grants import Grant, GrantKind, GrantScope, GrantStore, UserAuthority, timed_expiry
from scar.security.risk import ActionFacts


class ApprovalResponse(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_TASK = "allow_task"
    ALLOW_SESSION = "allow_session"
    ALLOW_TIMED = "allow_timed"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"
    DENY_ALWAYS = "deny_always"


ALLOWING = {
    ApprovalResponse.ALLOW_ONCE,
    ApprovalResponse.ALLOW_TASK,
    ApprovalResponse.ALLOW_SESSION,
    ApprovalResponse.ALLOW_TIMED,
    ApprovalResponse.ALLOW_ALWAYS,
}

KEYBOARD_CHANNELS = {"cli", "tui", "ipc-client"}


class ApprovalRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: new_id("apr"))
    task_id: str | None = None
    action_id: str
    tool: str
    args_hash: str
    risk: RiskLevel
    summary: str  # precise statement of exactly what will happen
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str
    critical: bool = False
    grantable: bool = True
    confirmation_code: str = ""
    readback: str = ""  # short spoken summary for voice
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timeout_s: float = 300.0

    def allowed_responses(self) -> list[ApprovalResponse]:
        if self.critical:
            return [ApprovalResponse.ALLOW_ONCE, ApprovalResponse.DENY]
        if not self.grantable:
            return [ApprovalResponse.ALLOW_ONCE, ApprovalResponse.DENY, ApprovalResponse.DENY_ALWAYS]
        return list(ApprovalResponse)


class ApprovalResolution(BaseModel):
    request_id: str
    response: ApprovalResponse
    channel: str
    args_hash: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    note: str = ""
    grant_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.response in ALLOWING


class ApprovalError(Exception):
    pass


class _Pending:
    def __init__(self, request: ApprovalRequest, facts: ActionFacts, future: asyncio.Future[ApprovalResolution]) -> None:
        self.request = request
        self.facts = facts
        self.future = future


class ApprovalBroker:
    def __init__(self, bus: EventBus, grants: GrantStore, audit: AuditLog, default_timeout: float = 300.0) -> None:
        self.bus = bus
        self.grants = grants
        self.audit = audit
        self.default_timeout = default_timeout
        self._pending: dict[str, _Pending] = {}
        self._channels: dict[str, int] = {}

    # --------------- channels ---------------
    def attach_channel(self, name: str) -> None:
        self._channels[name] = self._channels.get(name, 0) + 1

    def detach_channel(self, name: str) -> None:
        if self._channels.get(name, 0) > 1:
            self._channels[name] -= 1
        else:
            self._channels.pop(name, None)

    @property
    def channels(self) -> list[str]:
        return sorted(self._channels)

    def pending(self) -> list[ApprovalRequest]:
        return [p.request for p in self._pending.values()]

    def get(self, request_id: str) -> ApprovalRequest | None:
        p = self._pending.get(request_id)
        return p.request if p else None

    # --------------- request ---------------
    async def request(self, req: ApprovalRequest, facts: ActionFacts) -> ApprovalResolution:
        req.timeout_s = req.timeout_s or self.default_timeout
        if req.critical:
            req.confirmation_code = secrets.token_hex(2).upper()
        self.audit.append(
            "approval_requested",
            {"request_id": req.request_id, "tool": req.tool, "risk": req.risk.name, "summary": req.summary,
             "reason": req.reason, "args_hash": req.args_hash},
            req.task_id,
        )
        if not self._channels:
            res = ApprovalResolution(request_id=req.request_id, response=ApprovalResponse.DENY, channel="none",
                                     args_hash=req.args_hash, note="no interactive approval channel is attached")
            self._record(req, res)
            return res
        if req.critical and not (set(self._channels) & KEYBOARD_CHANNELS):
            res = ApprovalResolution(request_id=req.request_id, response=ApprovalResponse.DENY, channel="none",
                                     args_hash=req.args_hash, note="CRITICAL actions need keyboard confirmation")
            self._record(req, res)
            return res
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalResolution] = loop.create_future()
        self._pending[req.request_id] = _Pending(req, facts, fut)
        self.bus.publish(ApprovalRequested(task_id=req.task_id, request_id=req.request_id, risk=req.risk.name, message=req.summary))
        try:
            res = await asyncio.wait_for(asyncio.shield(fut), timeout=req.timeout_s)
        except TimeoutError:
            res = ApprovalResolution(request_id=req.request_id, response=ApprovalResponse.DENY, channel="timeout",
                                     args_hash=req.args_hash, note="approval timed out")
            self._record(req, res)
        except asyncio.CancelledError:
            res = ApprovalResolution(request_id=req.request_id, response=ApprovalResponse.DENY, channel="cancelled",
                                     args_hash=req.args_hash, note="task cancelled")
            self._record(req, res)
            raise
        finally:
            self._pending.pop(req.request_id, None)
        return res

    # --------------- resolve (called by user channels) ---------------
    def resolve(
        self,
        request_id: str,
        response: ApprovalResponse,
        channel: str,
        *,
        typed_confirmation: str | None = None,
        timed_minutes: float = 60.0,
        voice_readback_confirmed: bool = False,
    ) -> ApprovalResolution:
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            raise ApprovalError(f"no pending approval {request_id}")
        req = pending.request
        if response not in req.allowed_responses():
            raise ApprovalError(f"{response.value} is not allowed for this request")
        if req.critical and response in ALLOWING:
            if channel not in KEYBOARD_CHANNELS:
                raise ApprovalError("CRITICAL actions need confirmation typed at the keyboard")
            if (typed_confirmation or "").strip().upper() != req.confirmation_code:
                raise ApprovalError("confirmation code did not match")
        if channel == "voice" and response == ApprovalResponse.ALLOW_ALWAYS and not voice_readback_confirmed:
            raise ApprovalError("persistent permissions by voice need a read-back confirmation")
        grant_id: str | None = None
        if response not in (ApprovalResponse.ALLOW_ONCE, ApprovalResponse.DENY):
            grant = self._grant_for(req, pending.facts, response, timed_minutes)
            if grant is not None:
                self.grants.create(grant, UserAuthority("approval", detail=channel))
                grant_id = grant.grant_id
        res = ApprovalResolution(request_id=request_id, response=response, channel=channel, args_hash=req.args_hash, grant_id=grant_id)
        self._record(req, res)
        pending.future.set_result(res)
        return res

    def _grant_for(self, req: ApprovalRequest, facts: ActionFacts, response: ApprovalResponse, minutes: float) -> Grant | None:
        effect = "deny" if response == ApprovalResponse.DENY_ALWAYS else "allow"
        scope = {
            ApprovalResponse.ALLOW_TASK: GrantScope.TASK,
            ApprovalResponse.ALLOW_SESSION: GrantScope.SESSION,
            ApprovalResponse.ALLOW_TIMED: GrantScope.TIMED,
            ApprovalResponse.ALLOW_ALWAYS: GrantScope.PERSISTENT,
            ApprovalResponse.DENY_ALWAYS: GrantScope.PERSISTENT,
        }[response]
        max_risk = req.risk if req.risk < RiskLevel.CRITICAL else RiskLevel.HIGH
        if scope in (GrantScope.TASK, GrantScope.SESSION, GrantScope.TIMED):
            return Grant(
                effect=effect, kind=GrantKind.TOOL, tool=req.tool, scope=scope, task_id=req.task_id, max_risk=max_risk,
                expires_at=timed_expiry(minutes) if scope == GrantScope.TIMED else None,
            )
        # persistent: "always allow this exact pattern"
        if facts.command:
            return Grant(effect=effect, kind=GrantKind.COMMAND, tool=req.tool, match={"command_prefix": facts.command.strip()},
                         scope=scope, max_risk=max_risk)
        if facts.recipients and len(facts.recipients) == 1:
            return Grant(effect=effect, kind=GrantKind.RECIPIENT, tool=req.tool, match={"recipient": facts.recipients[0]},
                         scope=scope, max_risk=max_risk)
        if facts.app:
            return Grant(effect=effect, kind=GrantKind.APP, tool=req.tool, match={"app": facts.app}, scope=scope, max_risk=max_risk)
        if facts.domains and len(set(facts.domains)) == 1:
            return Grant(effect=effect, kind=GrantKind.DOMAIN, tool=req.tool, match={"domain": facts.domains[0]},
                         scope=scope, max_risk=max_risk)
        return Grant(effect=effect, kind=GrantKind.EXACT, tool=req.tool, args_hash=req.args_hash, scope=scope, max_risk=max_risk)

    def cancel_task(self, task_id: str) -> None:
        for p in list(self._pending.values()):
            if p.request.task_id == task_id and not p.future.done():
                res = ApprovalResolution(request_id=p.request.request_id, response=ApprovalResponse.DENY, channel="cancelled",
                                         args_hash=p.request.args_hash, note="task cancelled")
                self._record(p.request, res)
                p.future.set_result(res)

    def _record(self, req: ApprovalRequest, res: ApprovalResolution) -> None:
        self.audit.append(
            "approval_resolved",
            {"request_id": req.request_id, "tool": req.tool, "response": res.response.value, "channel": res.channel,
             "note": res.note, "grant_id": res.grant_id},
            req.task_id,
        )
        self.bus.publish(ApprovalResolved(task_id=req.task_id, request_id=req.request_id, decision=res.response.value, message=res.note))
