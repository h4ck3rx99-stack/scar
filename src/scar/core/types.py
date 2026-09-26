"""Core data model shared by every SCAR subsystem.

These models are the frozen interfaces between the agent, the tool pipeline,
the security layer and persistence. Change them only with an ADR.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scar.core.ids import new_id


def utcnow() -> datetime:
    return datetime.now(UTC)


class TrustLevel(StrEnum):
    SYSTEM = "system"
    USER = "user"
    RUNTIME = "runtime"
    MODEL = "model"
    TOOL_STRUCTURED = "tool_structured"
    UNTRUSTED_EXTERNAL = "untrusted_external"


class Provenance(BaseModel):
    """Where a piece of content came from and how far it can be trusted."""

    model_config = ConfigDict(frozen=True)

    source: str
    trust: TrustLevel
    detail: str = ""
    at: datetime = Field(default_factory=utcnow)

    @classmethod
    def user(cls, detail: str = "") -> Provenance:
        return cls(source="user", trust=TrustLevel.USER, detail=detail)

    @classmethod
    def runtime(cls, detail: str = "") -> Provenance:
        return cls(source="runtime", trust=TrustLevel.RUNTIME, detail=detail)

    @classmethod
    def structured(cls, source: str, detail: str = "") -> Provenance:
        return cls(source=source, trust=TrustLevel.TOOL_STRUCTURED, detail=detail)

    @classmethod
    def external(cls, source: str, detail: str = "") -> Provenance:
        return cls(source=source, trust=TrustLevel.UNTRUSTED_EXTERNAL, detail=detail)


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    def escalate(self, steps: int = 1) -> RiskLevel:
        return RiskLevel(min(int(self) + steps, int(RiskLevel.CRITICAL)))

    @classmethod
    def parse(cls, value: str | int | RiskLevel) -> RiskLevel:
        if isinstance(value, RiskLevel):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value.strip().upper()]


class SideEffect(StrEnum):
    NONE = "none"
    LOCAL = "local"
    EXTERNAL = "external"
    IRREVERSIBLE = "irreversible"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_USER = "awaiting_user"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }


class InputOrigin(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    SCHEDULER = "scheduler"


def hash_args(tool: str, args: dict[str, Any]) -> str:
    """Stable hash of a tool invocation; binds approvals to exact arguments."""
    payload = json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Check(BaseModel):
    """One deterministic postcondition check."""

    name: str
    passed: bool
    detail: str = ""


class VerificationResult(BaseModel):
    """Outcome of verifying an action or a task.

    ``verified`` is True when every check passed, False when any failed, and
    None when no deterministic check was possible (never rounded up to True).
    """

    verified: bool | None
    checks: list[Check] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    note: str = ""

    @classmethod
    def from_checks(cls, checks: list[Check], evidence: dict[str, Any] | None = None) -> VerificationResult:
        if not checks:
            return cls(verified=None, checks=[], evidence=evidence or {}, note="no deterministic check")
        return cls(verified=all(c.passed for c in checks), checks=checks, evidence=evidence or {})

    @classmethod
    def unverifiable(cls, note: str) -> VerificationResult:
        return cls(verified=None, note=note)


class ToolResult(BaseModel):
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    model_view: str = ""
    artifact_ref: str | None = None
    provenance: Provenance = Field(default_factory=Provenance.runtime)
    error_type: str | None = None
    verification: VerificationResult | None = None
    missing_prerequisite: str | None = None
    setup_doc: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.OK

    @classmethod
    def success(
        cls,
        summary: str,
        data: dict[str, Any] | None = None,
        *,
        provenance: Provenance | None = None,
        model_view: str = "",
    ) -> ToolResult:
        return cls(
            status=ToolStatus.OK,
            summary=summary,
            data=data or {},
            provenance=provenance or Provenance.runtime(),
            model_view=model_view,
        )

    @classmethod
    def failure(cls, summary: str, error_type: str, data: dict[str, Any] | None = None) -> ToolResult:
        return cls(status=ToolStatus.ERROR, summary=summary, error_type=error_type, data=data or {})

    @classmethod
    def unavailable(cls, prerequisite: str, setup_doc: str, summary: str = "") -> ToolResult:
        return cls(
            status=ToolStatus.UNAVAILABLE,
            summary=summary or f"Unavailable: {prerequisite}",
            error_type="CapabilityUnavailable",
            missing_prerequisite=prerequisite,
            setup_doc=setup_doc,
        )


class Action(BaseModel):
    action_id: str = Field(default_factory=lambda: new_id("act"))
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    args_hash: str = ""
    risk: RiskLevel = RiskLevel.LOW
    risk_reasons: list[str] = Field(default_factory=list)
    tainted_args: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    decision: PolicyDecision | None = None
    decision_reason: str = ""
    requested_by: str = "model"
    call_id: str | None = None

    def with_hash(self) -> Action:
        return self.model_copy(update={"args_hash": hash_args(self.tool, self.args)})


class Observation(BaseModel):
    action_id: str
    tool: str
    result: ToolResult
    at: datetime = Field(default_factory=utcnow)
    duration_ms: int = 0
    injection_flags: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    index: int
    intent: str
    candidate_tools: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    risk_notes: str = ""
    done: bool = False


class Plan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    completion_criteria: str = ""
    revision: int = 0


class BudgetUsage(BaseModel):
    steps: int = 0
    tool_calls: int = 0
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    wall_seconds: float = 0.0


class ProviderUsage(BaseModel):
    provider: str
    model: str
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    data_classes: list[str] = Field(default_factory=list)


class TaskState(BaseModel):
    task_id: str = Field(default_factory=lambda: new_id("task"))
    parent_task_id: str | None = None
    objective: str
    origin: InputOrigin = InputOrigin.TEXT
    autonomy_level: int = 3
    role: str = "executor"
    plan: Plan | None = None
    current_step: int = 0
    action_history: list[Action] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    retries: dict[str, int] = Field(default_factory=dict)
    verification: VerificationResult | None = None
    status: TaskStatus = TaskStatus.PENDING
    result_summary: str = ""
    budgets_used: BudgetUsage = Field(default_factory=BudgetUsage)
    provider_usage: list[ProviderUsage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    background: bool = False

    def touch(self) -> None:
        self.updated_at = utcnow()

    def record_usage(self, provider: str, model: str, tokens_in: int, tokens_out: int, data_class: str) -> None:
        for usage in self.provider_usage:
            if usage.provider == provider and usage.model == model:
                usage.calls += 1
                usage.tokens_in += tokens_in
                usage.tokens_out += tokens_out
                if data_class not in usage.data_classes:
                    usage.data_classes.append(data_class)
                break
        else:
            self.provider_usage.append(
                ProviderUsage(
                    provider=provider,
                    model=model,
                    calls=1,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    data_classes=[data_class],
                )
            )
        self.budgets_used.tokens_in += tokens_in
        self.budgets_used.tokens_out += tokens_out
