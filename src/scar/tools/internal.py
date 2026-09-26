"""Internal control tools: read_artifact, ask_user, finish."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from scar.agent.clarify import Question
from scar.core.errors import ToolError
from scar.core.types import ToolResult, TrustLevel
from scar.tools.base import Tool, ToolContext, ToolInput


class ReadArtifactInput(ToolInput):
    ref: str = Field(description="artifact://… handle from an earlier result")
    start_line: int = Field(1, ge=1)
    max_lines: int = Field(200, ge=1, le=2000)


class ReadArtifact(Tool):
    name = "read_artifact"
    description = "Read a range of lines from a large earlier output stored as artifact://…"
    input_model = ReadArtifactInput
    internal = True
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: ReadArtifactInput, ctx: ToolContext) -> ToolResult:
        text, total = ctx.services.artifacts.read_lines(args.ref, args.start_line, args.max_lines)
        end = min(total, args.start_line + args.max_lines - 1)
        return self.ok(f"Artifact lines {args.start_line}-{end} of {total}", {"ref": args.ref, "total_lines": total},
                       model_view=f"[{args.ref} lines {args.start_line}-{end} of {total}]\n{text}", source=args.ref)


class AskUserInput(ToolInput):
    question: str = Field(description="One short, specific question")
    options: list[str] = Field(default_factory=list, max_length=8, description="Choices, if the answer is one of a few")


class AskUser(Tool):
    name = "ask_user"
    description = ("Ask the user a clarifying question. Use when ambiguity could cause an external or destructive "
                   "action or the wrong target (e.g. two matching contacts or folders).")
    input_model = AskUserInput
    internal = True
    timeout = 900.0

    async def run(self, args: AskUserInput, ctx: ToolContext) -> ToolResult:
        broker = ctx.services.questions
        if broker is None or not broker.has_channel:
            raise ToolError("no interactive user is available to answer", "NoUserChannel")
        from scar.core.types import TaskStatus

        prev = ctx.task.status if ctx.task else None
        if ctx.task is not None:
            ctx.task.status = TaskStatus.AWAITING_USER
        try:
            answer = await broker.ask(Question(task_id=ctx.task.task_id if ctx.task else None, text=args.question,
                                               options=args.options))
        finally:
            if ctx.task is not None and prev is not None:
                ctx.task.status = prev
        if answer is None:
            raise ToolError("the user did not answer", "NoAnswer")
        # the user's answer is trusted user intent for scope and taint purposes
        ctx.taint.record_trusted(answer)
        if ctx.scope is not None:
            ctx.scope.add_user_text(answer)
            for opt in args.options:
                if opt.strip().lower() == answer.strip().lower():
                    ctx.scope.confirm_recipient(opt)
                    ctx.taint.confirm(opt)
        return ToolResult.success(f"User answered: {answer}", {"answer": answer},
                                  model_view=f"The user answered: {answer}")


class Evidence(BaseModel):
    kind: Literal["file", "process", "window", "url", "command", "message", "text", "tests", "other"]
    detail: dict[str, Any] = Field(default_factory=dict)


class FinishInput(ToolInput):
    summary: str = Field(description="Short user-facing result, e.g. 'Opened BISense in VS Code.'")
    status: Literal["done", "partial", "failed"] = "done"
    evidence: list[Evidence] = Field(default_factory=list, description="Facts proving the objective is met")


class Finish(Tool):
    name = "finish"
    description = ("End the task with a short user-facing summary. Only claim what earlier tool results verified; "
                   "the runtime re-checks the evidence.")
    input_model = FinishInput
    internal = True

    async def run(self, args: FinishInput, ctx: ToolContext) -> ToolResult:
        return ToolResult.success(args.summary, {"status": args.status,
                                                  "evidence": [e.model_dump() for e in args.evidence]})


TOOLS: list[type[Tool]] = [ReadArtifact, AskUser, Finish]
