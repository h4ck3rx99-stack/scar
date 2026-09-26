"""Memory tools (C9.11): remember, recall, forget, alias. Writes pass the memory write policy; writes derived from
external content are tainted and therefore need the user's confirmation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, VerificationResult
from scar.memory.extraction import extract
from scar.memory.policy import CATEGORIES
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput


def _mem(ctx: ToolContext):  # type: ignore[no-untyped-def]
    if ctx.services.memory is None:
        raise ToolError("memory is not available", "Unavailable")
    return ctx.services.memory


class RememberInput(ToolInput):
    text: str = Field(description="a short durable fact, e.g. 'BISense is at C:\\Projects\\BISense'")
    category: Literal["semantic", "preference", "project", "episodic", "alias"] = "semantic"
    importance: float = Field(0.6, ge=0, le=1)
    project: str | None = None


class MemoryRemember(Tool):
    name = "memory.remember"
    description = ("Store a durable, useful fact (preferences, project locations, tooling choices, aliases). Never "
                   "secrets. Do not store whole conversations.")
    input_model = RememberInput
    capabilities = ("memory.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("memory",)
    sensitive_args = {"text": "body"}
    data_class = "memory"

    def assess(self, args: RememberInput, ctx: ToolContext) -> RiskAssessment:
        return RiskAssessment(RiskLevel.MEDIUM)

    def describe(self, args: RememberInput) -> str:
        return f"remember: {args.text[:120]}"

    async def run(self, args: RememberInput, ctx: ToolContext) -> ToolResult:
        mem = _mem(ctx)
        ex = extract(args.text)
        trust = "user" if ctx.scope is not None and ctx.scope.mentions(args.text[:60]) else "model"
        try:
            if ex.alias and ex.target:
                mid = mem.set_alias(ex.alias, ex.target, ex.alias_kind or "folder")
                status = "saved"
            elif ex.preference and (args.project or ex.target):
                mid = mem.set_project_preference(args.project or str(ex.target), ex.preference[0], ex.preference[1])
                status = "saved"
            else:
                mid, status = mem.store(args.text, ex.category if args.category == "semantic" else args.category,
                                        key=ex.key, project=args.project, importance=args.importance, source="assistant",
                                        trust=trust)
        except ValueError as exc:
            raise ToolError(f"not stored: {exc}", "MemoryPolicy") from exc
        return self.ok(f"Remembered ({status})", {"id": mid, "status": status})

    async def verify(self, args: RememberInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name="memory stored", passed=_mem(ctx).get(result.data["id"]) is not None)])


class RecallInput(ToolInput):
    query: str
    k: int = Field(6, ge=1, le=30)
    category: str | None = Field(None, description=f"one of {', '.join(CATEGORIES)}")


class MemoryRecall(Tool):
    name = "memory.recall"
    description = "Search long-term memory (hybrid keyword + semantic)."
    input_model = RecallInput
    capabilities = ("memory.read",)
    categories = ("memory",)
    data_class = "memory"

    async def run(self, args: RecallInput, ctx: ToolContext) -> ToolResult:
        hits = _mem(ctx).search(args.query, k=args.k, categories=[args.category] if args.category else None)
        view = "\n".join(f"- [{m.category}] {m.text} (score {m.score:.2f}, id {m.id})" for m in hits)
        return self.ok(f"{len(hits)} memories", {"memories": [m.as_dict() for m in hits]}, model_view=view or "nothing relevant")


class ForgetInput(ToolInput):
    id: str | None = None
    query: str | None = Field(None, description="forget memories best matching this")


class MemoryForget(Tool):
    name = "memory.forget"
    description = "Delete a memory by id, or the memories best matching a description."
    input_model = ForgetInput
    capabilities = ("memory.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("memory",)

    def describe(self, args: ForgetInput) -> str:
        return f"forget {args.id or repr(args.query)}"

    async def run(self, args: ForgetInput, ctx: ToolContext) -> ToolResult:
        if not args.id and not args.query:
            raise ToolError("give id or query", "InvalidInput")
        removed = _mem(ctx).forget(args.id, args.query)
        return self.ok(f"Forgot {len(removed)} memor{'y' if len(removed) == 1 else 'ies'}", {"removed": removed})

    async def verify(self, args: ForgetInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name=f"{m} deleted", passed=_mem(ctx).get(m) is None) for m in result.data["removed"]])


class AliasInput(ToolInput):
    alias: str = Field(description="what the user calls it, e.g. 'my acceptance project'")
    target: str = Field(description="what it refers to: a folder path, app name or URL")
    kind: Literal["folder", "app", "url"] = "folder"


class MemoryAlias(Tool):
    name = "memory.alias"
    description = "Remember an alias: a name the user uses for a folder, app or URL."
    input_model = AliasInput
    capabilities = ("memory.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("memory",)
    sensitive_args = {"target": "path"}

    def describe(self, args: AliasInput) -> str:
        return f"remember that '{args.alias}' means {args.target}"

    async def run(self, args: AliasInput, ctx: ToolContext) -> ToolResult:
        mid = _mem(ctx).set_alias(args.alias, args.target, args.kind)
        return self.ok(f"'{args.alias}' now refers to {args.target}", {"id": mid})

    async def verify(self, args: AliasInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        got = _mem(ctx).resolve_alias(args.alias, args.kind)
        return VerificationResult.from_checks([Check(name="alias resolves", passed=got == args.target, detail=str(got))])


TOOLS: list[type[Tool]] = [MemoryRemember, MemoryRecall, MemoryForget, MemoryAlias]
