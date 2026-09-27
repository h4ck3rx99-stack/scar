"""The tool execution pipeline (C5). Every call from every agent follows it:

 1. schema validation          7. execution (timeout + cancellation)
 2. risk computation           8. output capture with byte caps
 3. taint and scope            9. sanitisation, truncation, provenance
 4. policy decision           10. postcondition verification
 5. approval (if required)    11. persistence and audit
 6. resource admission        12. event emission
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from scar.core.cancel import run_cancellable
from scar.core.errors import (
    ApprovalDenied,
    Cancelled,
    CapabilityUnavailable,
    FocusLost,
    PathViolation,
    PolicyDenied,
    ResourceDenied,
    ToolError,
)
from scar.core.events import ToolCalled, ToolCompleted
from scar.core.types import (
    Action,
    Observation,
    PolicyDecision,
    Provenance,
    RiskLevel,
    SideEffect,
    ToolResult,
    ToolStatus,
    TrustLevel,
    VerificationResult,
    hash_args,
)
from scar.security.approval import ApprovalRequest, ApprovalResolution
from scar.security.injection import detect, wrap_untrusted
from scar.security.policy import PolicyInput, PolicyOutcome
from scar.security.redaction import global_redactor
from scar.security.risk import RiskAssessment
from scar.storage.db import now_iso
from scar.tools.base import Tool, ToolContext
from scar.tools.registry import ToolRegistry

log = structlog.get_logger("scar.pipeline")

SUMMARY_MAX = 600
MODEL_VIEW_MAX = 8000
DATA_MAX = 48_000
_LOCAL_CLASSES = {"files", "memory", "clipboard", "screen", "email", "messages", "contacts"}


@dataclass
class PreparedCall:
    tool: Tool
    action: Action
    args: Any
    assessment: RiskAssessment
    outcome: PolicyOutcome


class ToolPipeline:
    def __init__(self, services: Any, registry: ToolRegistry) -> None:
        self.s = services
        self.registry = registry

    # ------------------------------------------------------------------ public
    async def execute(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        ctx: ToolContext,
        *,
        requested_by: str = "model",
        call_id: str | None = None,
        approved: ApprovalResolution | None = None,
    ) -> Observation:
        started = time.perf_counter()
        tool = self.registry.get(tool_name) or self.registry.by_wire_name(tool_name)
        action = Action(tool=tool_name, args=raw_args, requested_by=requested_by, call_id=call_id).with_hash()
        if tool is None:
            return self._finish(action, ToolResult.failure(f"Unknown tool {tool_name!r}", "UnknownTool"), ctx, started)
        action.tool = tool.name
        avail = self.registry.availability(tool.name)
        if not avail.ok:
            return self._finish(action, ToolResult.unavailable(avail.prerequisite, avail.setup_doc), ctx, started)

        # 1. schema validation
        try:
            args = tool.input_model.model_validate(raw_args)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'args'}: {e['msg']}" for e in exc.errors()[:6])
            return self._finish(action, ToolResult.failure(f"Invalid arguments: {msg}", "InvalidInput"), ctx, started)
        clean_args = args.model_dump(mode="json")
        action.args = clean_args
        action.args_hash = hash_args(tool.name, clean_args)

        # 2-4. risk, taint/scope, policy
        prepared = self._prepare(tool, action, args, ctx)
        action = prepared.action
        outcome = prepared.outcome
        self._persist_call(action, ctx)

        if outcome.decision == PolicyDecision.DENY:
            self.s.audit.append("action_denied", {"tool": tool.name, "reason": outcome.reason, "risk": outcome.risk.name,
                                                  "args_hash": action.args_hash}, ctx.task.task_id if ctx.task else None)
            return self._finish(action, ToolResult(status=ToolStatus.DENIED, summary=f"Not allowed: {outcome.reason}",
                                                   error_type="PolicyDenied"), ctx, started)

        # 5. approval
        if outcome.decision == PolicyDecision.ASK:
            if approved is not None and approved.allowed and approved.args_hash == action.args_hash and not outcome.critical_confirmation:
                resolution = approved
            else:
                if approved is not None and approved.args_hash != action.args_hash:
                    log.warning("approval_args_changed", tool=tool.name)
                resolution = await self._ask(tool, action, args, prepared.assessment, outcome, ctx)
            if not resolution.allowed:
                note = resolution.note or "you declined"
                return self._finish(action, ToolResult(status=ToolStatus.DENIED, summary=f"Not approved: {note}",
                                                       error_type="ApprovalDenied"), ctx, started)
            # TOCTOU: the arguments that run must be exactly the ones approved
            if hash_args(tool.name, args.model_dump(mode="json")) != resolution.args_hash:
                return self._finish(action, ToolResult(status=ToolStatus.DENIED, summary="Arguments changed after approval; "
                                                       "re-approval required", error_type="ApprovalMismatch"), ctx, started)
            action.decision = PolicyDecision.ALLOW
            action.decision_reason = f"approved by user via {resolution.channel}"
        elif outcome.grant is not None and self.s.grants is not None:
            self.s.grants.consume(outcome.grant)

        if prepared.assessment.level >= RiskLevel.HIGH:
            self.s.audit.append("action_executed", {"tool": tool.name, "risk": prepared.assessment.level.name,
                                                    "summary": tool.describe(args), "args_hash": action.args_hash,
                                                    "decision": action.decision_reason},
                                ctx.task.task_id if ctx.task else None)

        # dry run: side-effecting tools report what they would do
        if ctx.dry_run and tool.side_effects != SideEffect.NONE:
            return self._finish(action, tool.dry_run(args), ctx, started)

        # 6-7. admission + execution
        self.s.bus.publish(ToolCalled(task_id=ctx.task.task_id if ctx.task else None, tool=tool.name,
                                      action_id=action.action_id, risk=prepared.assessment.level.name,
                                      message=tool.progress_line(args) or ""))
        line = tool.progress_line(args)
        if line:
            ctx.say(line)
        result = await self._run(tool, args, ctx)

        # 8-9. capture, sanitise, provenance
        result, flags = self._post_process(tool, result, ctx)

        # 10. postconditions
        if result.status == ToolStatus.OK and tool.side_effects != SideEffect.NONE:
            try:
                verification = await asyncio.wait_for(tool.verify(args, result, ctx), timeout=max(10.0, tool.timeout))
            except TimeoutError:
                verification = VerificationResult.unverifiable("verification timed out")
            except (ToolError, OSError) as exc:
                verification = VerificationResult(verified=False, note=f"verification error: {exc}")
            result.verification = verification
            if verification is not None and verification.verified is False:
                failed = [c.name + (f" ({c.detail})" if c.detail else "") for c in verification.checks if not c.passed]
                result.status = ToolStatus.ERROR
                result.error_type = "VerificationFailed"
                result.summary = f"{result.summary} — but verification failed: {', '.join(failed) or verification.note}"
        elif result.status == ToolStatus.OK and result.verification is None:
            result.verification = await tool.verify(args, result, ctx) if tool.side_effects == SideEffect.NONE else None
        obs = self._finish(action, result, ctx, started)
        obs.injection_flags = flags
        return obs

    # ------------------------------------------------------------------ steps
    def _prepare(self, tool: Tool, action: Action, args: Any, ctx: ToolContext) -> PreparedCall:
        try:
            assessment = tool.assess(args, ctx)
        except PathViolation as exc:
            assessment = RiskAssessment(RiskLevel.CRITICAL)
            assessment.deny(f"path violation: {exc.reason}")
        if tool.base_risk > assessment.level:
            assessment.raise_to(tool.base_risk, "")

        tainted: list[str] = []
        out_of_scope: list[str] = []
        if tool.sensitive_args:
            hits = ctx.taint.check_args(action.args, tool.sensitive_args.keys())
            for h in hits:
                kind = tool.sensitive_args.get(h.arg.split(".", 1)[0].split("[", 1)[0], "value")
                if tool.side_effects == SideEffect.NONE and kind in ("path", "url", "domain"):
                    # reading a file or page that a search/listing/page pointed to is normal agent work, not one of the
                    # tainted-action cases (C4.9: communication, commands, deletion/overwrite, form navigation).
                    # Anything read stays tracked, and exfiltration control guards where it can go next.
                    log.debug("taint_read_only", tool=tool.name, arg=h.arg, sources=h.sources[:2])
                    continue
                tainted.append(f"{kind} {h.value!r} came from {', '.join(h.sources[:2])}")
            if hits and tool.side_effects in (SideEffect.LOCAL, SideEffect.IRREVERSIBLE) and any(
                tool.sensitive_args.get(h.arg.split(".", 1)[0], "") in ("path", "command") for h in hits
            ):
                assessment.raise_to(assessment.level.escalate(1), "destructive arguments derived from external content")

        # memory writes after reading external content need the user's confirmation (not grantable)
        if any(c.startswith("memory.write") for c in tool.capabilities) and ctx.taint.untrusted_chars > 0:
            texts = [v for _, v in _string_args(action.args)]
            if not all(ctx.scope is not None and ctx.scope.mentions(t[:80]) for t in texts if t):
                tainted.append("memory write in a task that has read external content")

        # exfiltration control
        if tool.side_effects == SideEffect.EXTERNAL or any(k in ("url", "domain") for k in tool.sensitive_args.values()):
            outgoing = [v for k, v in _string_args(action.args) if tool.sensitive_args.get(k.split(".", 1)[0]) in
                        ("body", "url", "attachment", "domain", "recipient", None) or tool.side_effects == SideEffect.EXTERNAL]
            for value in outgoing:
                kinds = global_redactor().contains_secret(value)
                if kinds:
                    assessment.deny(f"outgoing data contains secrets ({', '.join(sorted(set(kinds))[:3])})", "exfiltrate_scar_secrets")
                    break
                local = ctx.taint.local_sources_of(value)
                if local and ctx.scope is not None and not any(ctx.scope.mentions(d) for d in assessment.facts.domains + assessment.facts.recipients):
                    out_of_scope.append(f"sends local {', '.join(local)} content to a destination you did not specify")
                    break

        if ctx.scope is not None:
            for cap in tool.capabilities:
                res = ctx.scope.capability_in_scope(cap)
                if not res.in_scope:
                    out_of_scope.extend(res.reasons)
            if assessment.facts.external_destination or any(c.startswith("comms.send") for c in tool.capabilities):
                for rcpt in assessment.facts.recipients:
                    res = ctx.scope.recipient_in_scope(rcpt, assessment.facts.recipient_names)
                    if not res.in_scope:
                        out_of_scope.extend(res.reasons)

        settings = self.s.settings
        inp = PolicyInput(
            tool=tool.name,
            capabilities=list(tool.capabilities),
            args_hash=action.args_hash,
            assessment=assessment,
            side_effects=tool.side_effects,
            autonomy_level=ctx.task.autonomy_level if ctx.task else settings.autonomy_level,
            task_id=ctx.task.task_id if ctx.task else None,
            tainted=tainted,
            out_of_scope=out_of_scope,
            require_approval=settings.require_approval,
            internal=tool.internal,
        )
        outcome = self.s.policy.decide(inp)
        action.risk = outcome.risk
        action.risk_reasons = assessment.reasons
        action.tainted_args = tainted
        action.out_of_scope = out_of_scope
        action.decision = outcome.decision
        action.decision_reason = outcome.reason
        assessment.level = max(assessment.level, outcome.risk)
        if not tainted and tool.sensitive_args and outcome.decision == PolicyDecision.ALLOW:
            ctx.taint.record_own([v for k, v in _string_args(action.args) if k.split(".", 1)[0].split("[", 1)[0]
                                  in tool.sensitive_args])
        return PreparedCall(tool, action, args, assessment, outcome)

    async def _ask(
        self, tool: Tool, action: Action, args: Any, assessment: RiskAssessment, outcome: PolicyOutcome, ctx: ToolContext
    ) -> ApprovalResolution:
        details = global_redactor().redact_obj(tool.approval_details(args))
        if assessment.facts.paths:
            details.setdefault("paths", assessment.facts.paths[:20])
            if len(assessment.facts.paths) > 20 or assessment.facts.file_count:
                details["file_count"] = max(assessment.facts.file_count, len(assessment.facts.paths))
        if assessment.facts.command:
            details.setdefault("command", assessment.facts.command)
        details["risk_reasons"] = assessment.reasons[:5]
        req = ApprovalRequest(
            task_id=ctx.task.task_id if ctx.task else None,
            action_id=action.action_id,
            tool=tool.name,
            args_hash=action.args_hash,
            risk=assessment.level,
            summary=global_redactor().redact(tool.describe(args)),
            details=details,
            reason=outcome.reason,
            critical=outcome.critical_confirmation,
            grantable=outcome.grantable,
            readback=global_redactor().redact(tool.describe(args)),
            timeout_s=self.s.settings.approval_timeout,
        )
        if ctx.task is not None:
            from scar.core.types import TaskStatus

            prev = ctx.task.status
            ctx.task.status = TaskStatus.AWAITING_APPROVAL
            try:
                res = await self.s.approvals.request(req, assessment.facts)
            finally:
                ctx.task.status = prev
        else:
            res = await self.s.approvals.request(req, assessment.facts)
        if res.allowed:
            ctx.taint.record_trusted(" ".join(assessment.facts.recipients))
            for r in assessment.facts.recipients:
                ctx.taint.confirm(r)
                if ctx.scope is not None:
                    ctx.scope.confirm_recipient(r)
        return res

    async def _run(self, tool: Tool, args: Any, ctx: ToolContext) -> ToolResult:
        timeout = tool.timeout
        if tool.timeout_field:
            requested = getattr(args, tool.timeout_field, None)
            if isinstance(requested, int | float) and requested > 0:
                timeout = min(float(requested), float(self.s.settings.command_timeout_max))
        try:
            async with self._slot(tool):
                return await asyncio.wait_for(run_cancellable(tool.run(args, ctx), ctx.cancel), timeout=timeout + 5.0)
        except TimeoutError:
            return ToolResult(status=ToolStatus.TIMEOUT, summary=f"Timed out after {timeout:.0f}s", error_type="Timeout")
        except Cancelled as exc:
            return ToolResult(status=ToolStatus.CANCELLED, summary=f"Cancelled: {exc.reason}", error_type="Cancelled")
        except CapabilityUnavailable as exc:
            return ToolResult.unavailable(exc.prerequisite, exc.setup_doc, summary=str(exc))
        except (PolicyDenied, ApprovalDenied) as exc:
            return ToolResult(status=ToolStatus.DENIED, summary=str(exc), error_type=type(exc).__name__)
        except PathViolation as exc:
            return ToolResult(status=ToolStatus.DENIED, summary=f"Path not allowed: {exc}", error_type="PathViolation")
        except ResourceDenied as exc:
            return ToolResult.failure(f"Not enough resources right now: {exc.reason}", "ResourceDenied")
        except FocusLost as exc:
            return ToolResult.failure(f"Stopped: {exc}", "FocusLost")
        except ToolError as exc:
            return ToolResult.failure(str(exc), exc.error_type)
        except (OSError, ValueError, RuntimeError, LookupError, TypeError) as exc:
            log.warning("tool_exception", tool=tool.name, error=str(exc), error_type=type(exc).__name__)
            return ToolResult.failure(f"{type(exc).__name__}: {exc}", type(exc).__name__)
        except Exception as exc:
            log.exception("tool_internal_error", tool=tool.name)
            return ToolResult.failure(f"internal error in {tool.name}: {type(exc).__name__}: {exc}", "InternalError")

    @contextlib.asynccontextmanager
    async def _slot(self, tool: Tool) -> AsyncIterator[None]:
        adm = getattr(self.s, "admission", None)
        sem = None
        if adm is not None and tool.resource_slot:
            sem = {"subprocess": adm.subprocesses, "browser": adm.browser_contexts, "model": adm.model_calls}.get(tool.resource_slot)
        if sem is None:
            yield
            return
        async with adm.slot(sem):
            yield

    def _post_process(self, tool: Tool, result: ToolResult, ctx: ToolContext) -> tuple[ToolResult, list[str]]:
        red = global_redactor()
        task_id = ctx.task.task_id if ctx.task else "adhoc"
        result.summary = red.redact(result.summary)
        if len(result.summary) > SUMMARY_MAX:
            result.summary = result.summary[: SUMMARY_MAX - 1] + "…"
        view = result.model_view or result.summary
        view = red.redact(view)
        # data cap
        data_json = json.dumps(result.data, default=str, ensure_ascii=False)
        if len(data_json) > DATA_MAX:
            info = self.s.artifacts.put_text(task_id, data_json, ".json")
            result.data = {"truncated": True, "artifact_ref": info.ref, "size": info.size,
                           "preview": data_json[:2000]}
            result.artifact_ref = result.artifact_ref or info.ref
        else:
            result.data = red.redact_obj(result.data)
        if len(view) > MODEL_VIEW_MAX:
            info = self.s.artifacts.put_text(task_id, view)
            result.artifact_ref = result.artifact_ref or info.ref
            head = view[: MODEL_VIEW_MAX - 800]
            view = (f"{head}\n…[truncated: {len(view)} chars total, {info.lines} lines. Full content at {info.ref}; "
                    f"use read_artifact to read ranges]")
        flags: list[str] = []
        untrusted = result.provenance.trust == TrustLevel.UNTRUSTED_EXTERNAL or tool.output_trust == TrustLevel.UNTRUSTED_EXTERNAL
        if untrusted and result.status == ToolStatus.OK:
            if result.provenance.trust != TrustLevel.UNTRUSTED_EXTERNAL:
                result.provenance = Provenance.external(tool.name)
            full_text = view + " " + " ".join(v for _, v in _string_args(result.data))
            flags = detect(full_text)
            ctx.taint.record_untrusted(full_text, result.provenance.source)
            if flags:
                log.warning("injection_indicators", tool=tool.name, flags=flags, source=result.provenance.source[:120])
            view = wrap_untrusted(view, result.provenance, flags)
            if tool.data_class in _LOCAL_CLASSES:
                # local files/emails/screens are both untrusted *and* private: record for exfiltration control
                ctx.taint.record_local(full_text, tool.data_class)
        elif tool.data_class in _LOCAL_CLASSES and result.status == ToolStatus.OK:
            ctx.taint.record_local(view + " " + " ".join(v for _, v in _string_args(result.data)), tool.data_class)
        result.model_view = view
        return result, flags

    def _persist_call(self, action: Action, ctx: ToolContext) -> None:
        db = self.s.db
        args_json = json.dumps(global_redactor().redact_obj(action.args), default=str)[:20000]
        db.execute(
            "INSERT OR REPLACE INTO tool_calls(action_id, task_id, tool, args_json, args_hash, risk, tainted, decision, "
            "decision_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (action.action_id, ctx.task.task_id if ctx.task else None, action.tool, args_json, action.args_hash,
             action.risk.name, json.dumps(action.tainted_args), action.decision.value if action.decision else None,
             action.decision_reason, now_iso()),
        )

    def _finish(self, action: Action, result: ToolResult, ctx: ToolContext, started: float) -> Observation:
        duration = int((time.perf_counter() - started) * 1000)
        if not result.model_view:
            result.model_view = global_redactor().redact(result.summary)
        verified = result.verification.verified if result.verification else None
        try:
            updated = self.s.db.execute(
                "UPDATE tool_calls SET status = ?, summary = ?, error_type = ?, verified = ?, duration_ms = ?, decision = ?, "
                "decision_reason = ? WHERE action_id = ?",
                (result.status.value, global_redactor().redact(result.summary)[:2000], result.error_type,
                 None if verified is None else int(verified), duration,
                 action.decision.value if action.decision else None, action.decision_reason, action.action_id),
            )
            if not updated:
                self._persist_call(action, ctx)
                self.s.db.execute("UPDATE tool_calls SET status = ?, summary = ?, error_type = ?, duration_ms = ? WHERE action_id = ?",
                                  (result.status.value, result.summary[:2000], result.error_type, duration, action.action_id))
        except Exception as exc:  # noqa: BLE001 - persistence failure must not lose the observation
            log.error("persist_tool_call_failed", error=str(exc))
        self.s.metrics.record("tool.duration_ms", duration, tool=action.tool, status=result.status.value)
        self.s.bus.publish(ToolCompleted(task_id=ctx.task.task_id if ctx.task else None, tool=action.tool,
                                         action_id=action.action_id, status=result.status.value, duration_ms=duration,
                                         verified=verified, message=result.summary))
        obs = Observation(action_id=action.action_id, tool=action.tool, result=result, duration_ms=duration)
        if ctx.task is not None:
            ctx.task.action_history.append(action)
            ctx.task.observations.append(obs)
            ctx.task.budgets_used.tool_calls += 1
            if len(ctx.task.observations) > 60:
                del ctx.task.observations[: len(ctx.task.observations) - 60]
            if len(ctx.task.action_history) > 120:
                del ctx.task.action_history[: len(ctx.task.action_history) - 120]
            ctx.task.touch()
        return obs


def _string_args(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_string_args(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list | tuple):
        for i, v in enumerate(obj[:500]):
            out.extend(_string_args(v, f"{prefix}[{i}]"))
    elif isinstance(obj, str):
        out.append((prefix, obj))
    return out
