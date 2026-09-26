"""Planner (C8.3): structured plans for multi-step objectives; revisable; trivial tasks skip planning."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from scar.agent.prompts import load
from scar.core.events import PlanCreated
from scar.core.types import Plan, PlanStep, TaskState
from scar.providers.base import ChatMessage
from scar.providers.errors import AllProvidersFailed, ProviderError
from scar.providers.llm.structured import structured_chat

_MULTI = re.compile(r"(?i)\b(and then|then|after that|afterwards|, and|and (also )?(email|send|save|run|tell|commit|push|open|"
                    r"summari[sz]e|fix|create|write|start|notify))\b")


class _Step(BaseModel):
    intent: str
    candidate_tools: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    risk_notes: str = ""


class _PlanModel(BaseModel):
    steps: list[_Step] = Field(min_length=1, max_length=10)
    completion_criteria: str = ""


def needs_plan(objective: str) -> bool:
    words = len(objective.split())
    return bool(_MULTI.search(objective)) or words > 28


class Planner:
    def __init__(self, services: Any) -> None:
        self.s = services

    async def plan(self, task: TaskState, tool_names: list[str]) -> Plan | None:
        msgs = [ChatMessage(role="system", content=load("planner")),
                ChatMessage(role="user", content=f"Objective: {task.objective}\n\nAvailable tools: {', '.join(sorted(tool_names))}")]
        try:
            pm = await structured_chat(self.s.router, "reasoning", msgs, _PlanModel, task=task, max_tokens=1200)
        except (AllProvidersFailed, ProviderError):
            return None
        plan = Plan(steps=[PlanStep(index=i + 1, intent=s.intent, candidate_tools=s.candidate_tools,
                                    success_criteria=s.success_criteria, risk_notes=s.risk_notes)
                           for i, s in enumerate(pm.steps)], completion_criteria=pm.completion_criteria)
        self.s.bus.publish(PlanCreated(task_id=task.task_id, steps=[s.intent for s in plan.steps],
                                       message=f"Plan: {len(plan.steps)} steps"))
        return plan

    async def revise(self, task: TaskState, failure: str, tool_names: list[str]) -> Plan | None:
        done = [s.intent for s in task.plan.steps if s.done] if task.plan else []
        recent = "\n".join(f"- {o.tool}: [{o.result.status.value}] {o.result.summary}" for o in task.observations[-8:])
        msgs = [ChatMessage(role="system", content=load("planner")),
                ChatMessage(role="user", content=(f"Objective: {task.objective}\nAlready done: {done}\nRecent results:\n{recent}\n"
                                                  f"Problem: {failure}\nMake a revised plan for the remaining work.\n"
                                                  f"Available tools: {', '.join(sorted(tool_names))}"))]
        try:
            pm = await structured_chat(self.s.router, "reasoning", msgs, _PlanModel, task=task, max_tokens=1200)
        except (AllProvidersFailed, ProviderError):
            return None
        rev = (task.plan.revision + 1) if task.plan else 1
        return Plan(steps=[PlanStep(index=i + 1, intent=s.intent, candidate_tools=s.candidate_tools,
                                    success_criteria=s.success_criteria, risk_notes=s.risk_notes)
                           for i, s in enumerate(pm.steps)], completion_criteria=pm.completion_criteria, revision=rev)
