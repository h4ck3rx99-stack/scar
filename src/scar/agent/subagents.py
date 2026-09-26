"""SCAR sub-agents (C8.12): role-specialised child tasks for research fan-out, coder/tester/reviewer loops,
vision analysis and security review. Children use the same registry (filtered by role), permission engine,
resource admission and provider router; they cannot approve, grant or raise autonomy."""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from scar.core.types import RiskLevel, ToolResult, TrustLevel
from scar.security.risk import RiskAssessment
from scar.tools.base import Tool, ToolContext, ToolInput

Role = Literal["researcher", "coder", "tester", "reviewer", "vision_analyst", "security_reviewer"]


class SubTask(BaseModel):
    role: Role
    objective: str = Field(description="self-contained instruction for the sub-agent")


class DelegateInput(ToolInput):
    tasks: list[SubTask] = Field(min_length=1, max_length=5)
    parallel: bool = Field(True, description="run independent sub-tasks concurrently (bounded)")


class AgentDelegate(Tool):
    name = "agent.delegate"
    description = ("Delegate self-contained sub-tasks to specialised sub-agents (researcher, coder, tester, reviewer, "
                   "vision_analyst, security_reviewer). Use for research across several sources or code/test/review loops. "
                   "Returns each sub-agent's summary.")
    input_model = DelegateInput
    capabilities = ("agent.delegate",)
    base_risk = RiskLevel.LOW
    categories = ("web", "dev", "code", "research", "vision")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL  # summaries can carry web/file content
    timeout = 1900.0

    def assess(self, args: DelegateInput, ctx: ToolContext) -> RiskAssessment:
        # delegation itself is harmless: every child action is assessed individually by the same pipeline
        return RiskAssessment(RiskLevel.LOW)

    def describe(self, args: DelegateInput) -> str:
        return "delegate: " + "; ".join(f"{t.role}: {t.objective[:60]}" for t in args.tasks)

    async def run(self, args: DelegateInput, ctx: ToolContext) -> ToolResult:
        tm = ctx.services.tasks
        if tm is None or ctx.task is None:
            return ToolResult.failure("sub-agents are not available here", "Unavailable")
        if ctx.task.parent_task_id is not None:
            return ToolResult.failure("sub-agents cannot spawn further sub-agents", "NotAllowed")
        limit = ctx.services.settings.max_subagents
        sem = asyncio.Semaphore(max(1, limit))

        async def one(sub: SubTask) -> tuple[SubTask, str, str]:
            async with sem:
                ctx.say(f"{sub.role.replace('_', ' ').title()} working.")
                child = await tm.run_subagent(ctx.task, sub.role, sub.objective, ctx)
                return sub, child.status.value, child.result_summary

        if args.parallel:
            results = await asyncio.gather(*(one(t) for t in args.tasks))
        else:
            results = [await one(t) for t in args.tasks]
        view = "\n\n".join(f"[{t.role} — {status}] {t.objective[:80]}\n{summary}" for t, status, summary in results)
        return self.ok(f"{len(results)} sub-agent(s) finished", {"results": [{"role": t.role, "objective": t.objective,
                                                                             "status": s, "summary": m} for t, s, m in results]},
                       model_view=view)


TOOLS: list[type[Tool]] = [AgentDelegate]
