"""Tool base class (C5).

A tool declares its schema, capabilities, risk function, side effects,
timeout, requirements, executor, postconditions and dry-run behaviour.
Adding a tool = one module + one registration line; the agent core does not
change.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from scar.core.cancel import CancelToken
from scar.core.types import (
    Provenance,
    RiskLevel,
    SideEffect,
    TaskState,
    ToolResult,
    TrustLevel,
    VerificationResult,
)
from scar.security.risk import RiskAssessment
from scar.security.scope import ScopeAnchor
from scar.security.taint import TaintTracker


class ToolInput(BaseModel):
    """Base for tool input models: strict, no unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class NoInput(ToolInput):
    pass


@dataclass(frozen=True)
class Availability:
    ok: bool
    prerequisite: str = ""
    setup_doc: str = ""

    @classmethod
    def yes(cls) -> Availability:
        return cls(True)

    @classmethod
    def no(cls, prerequisite: str, setup_doc: str) -> Availability:
        return cls(False, prerequisite, setup_doc)


@dataclass(frozen=True)
class Requires:
    platform: str | None = None  # "win32" to require Windows
    modules: tuple[str, ...] = ()
    setting: str | None = None  # boolean Settings attribute that must be true
    credentials: tuple[str, ...] = ()  # secret names; any one satisfies
    setup_doc: str = "docs/troubleshooting.md"


ProgressFn = Callable[[str], None]


@dataclass
class ToolContext:
    """Everything a tool may use while executing one call."""

    services: Any  # scar.runtime.runtime.Services (kept untyped here to avoid an import cycle)
    cancel: CancelToken
    task: TaskState | None = None
    taint: TaintTracker = field(default_factory=TaintTracker)
    scope: ScopeAnchor | None = None
    role: str = "executor"
    dry_run: bool = False
    progress: ProgressFn | None = None
    session_id: str = ""

    @property
    def task_id(self) -> str:
        return self.task.task_id if self.task else "adhoc"

    def say(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)


class Tool(ABC):
    """Base class for every SCAR tool."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[ToolInput]] = NoInput
    output_model: ClassVar[type[BaseModel] | None] = None
    capabilities: ClassVar[tuple[str, ...]] = ()
    base_risk: ClassVar[RiskLevel] = RiskLevel.LOW
    side_effects: ClassVar[SideEffect] = SideEffect.NONE
    timeout: ClassVar[float] = 60.0
    requires: ClassVar[Requires] = Requires()
    # arg name -> kind (recipient | url | path | command | body | attachment | domain)
    sensitive_args: ClassVar[dict[str, str]] = {}
    output_trust: ClassVar[TrustLevel] = TrustLevel.TOOL_STRUCTURED
    data_class: ClassVar[str] = "general"
    categories: ClassVar[tuple[str, ...]] = ()
    internal: ClassVar[bool] = False
    supports_dry_run: ClassVar[bool] = True
    resource_slot: ClassVar[str | None] = None  # "subprocess" | "browser" | "model"
    # when set, the tool may take a per-call timeout from this input field (bounded by settings)
    timeout_field: ClassVar[str | None] = None

    def __init__(self, services: Any = None) -> None:
        self.services = services

    # ---------------- availability ----------------
    def availability(self) -> Availability:
        req = self.requires
        if req.platform and sys.platform != req.platform:
            return Availability.no(f"requires {req.platform}", req.setup_doc)
        for mod in req.modules:
            try:
                __import__(mod)
            except ImportError:
                return Availability.no(f"Python module {mod!r} is not installed", req.setup_doc)
        settings = getattr(self.services, "settings", None)
        if req.setting and settings is not None and not bool(getattr(settings, req.setting, True)):
            return Availability.no(f"disabled by SCAR_{req.setting.upper()}=false", "docs/configuration.md")
        secrets = getattr(self.services, "secrets", None)
        if req.credentials and secrets is not None and not any(secrets.has(c) for c in req.credentials):
            return Availability.no(f"missing credential: {' or '.join(req.credentials)}", req.setup_doc)
        return self.extra_availability()

    def extra_availability(self) -> Availability:
        return Availability.yes()

    # ---------------- risk ----------------
    def assess(self, args: Any, ctx: ToolContext) -> RiskAssessment:
        return RiskAssessment(self.base_risk)

    # ---------------- describe (approvals, UX) ----------------
    def describe(self, args: Any) -> str:
        return f"{self.name}"

    def approval_details(self, args: Any) -> dict[str, Any]:
        return args.model_dump(mode="json") if isinstance(args, BaseModel) else {}

    def progress_line(self, args: Any) -> str | None:
        """Short user-facing milestone shown when the tool starts (None = silent)."""
        return None

    # ---------------- execution ----------------
    @abstractmethod
    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        """Execute the tool. Concrete tools implement this."""

    async def verify(self, args: Any, result: ToolResult, ctx: ToolContext) -> VerificationResult | None:
        """Deterministic postconditions. None means the tool has none (read-only tools)."""
        return None

    def dry_run(self, args: Any) -> ToolResult:
        return ToolResult.success(f"[dry run] would {self.describe(args)}", {"dry_run": True})

    # ---------------- helpers ----------------
    def ok(self, summary: str, data: dict[str, Any] | None = None, *, model_view: str = "", source: str | None = None) -> ToolResult:
        prov = (
            Provenance.external(source or self.name)
            if self.output_trust == TrustLevel.UNTRUSTED_EXTERNAL
            else Provenance.structured(source or self.name)
        )
        return ToolResult.success(summary, data, provenance=prov, model_view=model_view)

    @classmethod
    def schema(cls) -> dict[str, Any]:
        return cls.input_model.model_json_schema()

    @classmethod
    def output_schema(cls) -> dict[str, Any] | None:
        return cls.output_model.model_json_schema() if cls.output_model else None


AsyncFn = Callable[..., Awaitable[Any]]
