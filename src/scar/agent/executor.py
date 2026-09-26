"""ReAct executor loop (C8.4) with verification, budgets, loop prevention and the recovery ladder (C8.6, C8.7)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from scar.agent.context import LOCAL_BUDGET_TOKENS, ContextBuilder
from scar.agent.planner import Planner
from scar.agent.verifier import honest_summary, verify_finish
from scar.core.budgets import BudgetTracker
from scar.core.errors import BudgetExceeded, Cancelled
from scar.core.events import StepStarted, TaskProgress
from scar.core.types import Observation, TaskState, TaskStatus, ToolStatus, VerificationResult, hash_args
from scar.providers.base import ToolCall
from scar.providers.errors import AllProvidersFailed, ProviderError, ProviderErrorKind
from scar.providers.llm.structured import parse_json_action
from scar.tools.base import ToolContext
from scar.tools.pipeline import ToolPipeline
from scar.tools.registry import ToolRegistry, wire_name

log = structlog.get_logger("scar.executor")

CATEGORY_HINTS: list[tuple[str, set[str]]] = [
    (r"\b(browser|website|web ?page|site|url|https?://|google|chrome|edge|youtube|link|form|log ?in|tab)\b", {"browser", "web"}),
    (r"\b(research|look up|find (out|information)|search (the web|online|for)|news|compare|summari[sz]e)\b", {"web", "documents"}),
    (r"\b(e-?mail|inbox|gmail|outlook|mail)\b", {"email", "contacts", "documents"}),
    (r"\b(message|text|telegram|discord|whatsapp|dm|chat)\b", {"messaging", "contacts"}),
    (r"\b(calendar|meeting|event|appointment|schedule a|invite)\b", {"calendar", "contacts"}),
    (r"\b(test|tests|build|compile|lint|git|commit|branch|push|pull request|pr|repo|repository|code|bug|fix|refactor|"
     r"npm|pnpm|pip|python|dev server|server|deploy)\b", {"dev", "git", "terminal", "fs", "code"}),
    (r"\b(screen|click|button|window|app|application|dialog|menu|type|press|mouse|keyboard|minimi[sz]e|maximi[sz]e)\b",
     {"screen", "vision", "windows", "input", "apps"}),
    (r"\b(open|launch|start|close|quit)\b", {"apps", "windows"}),
    (r"\b(remind|reminder|schedule|every (day|week|morning)|tomorrow|at \d)\b", {"schedule"}),
    (r"\b(watch|monitor|crash|notify|tell me when|let me know when|alert)\b", {"monitor", "notify", "process"}),
    (r"\b(file|folder|directory|document|pdf|docx|report|save|write|read|note|csv|spreadsheet)\b", {"fs", "documents"}),
    (r"\b(process|cpu|ram|memory usage|gpu|battery|disk|system|kill)\b", {"system", "process"}),
    (r"\b(clipboard|copy|paste)\b", {"clipboard"}),
    (r"\b(remember|forget|alias|preference)\b", {"memory"}),
    (r"\b(command|terminal|powershell|cmd|script|run)\b", {"terminal", "fs"}),
]
BASE_CATEGORIES = {"fs", "memory"}


def categories_for(objective: str) -> set[str]:
    cats = set(BASE_CATEGORIES)
    low = objective.lower()
    for pattern, add in CATEGORY_HINTS:
        if re.search(pattern, low):
            cats |= add
    if cats == BASE_CATEGORIES:
        cats |= {"terminal", "apps", "windows", "screen", "web", "system", "documents"}
    return cats


@dataclass
class ExecOutcome:
    status: TaskStatus
    summary: str
    verification: VerificationResult | None


class Executor:
    def __init__(self, services: Any, registry: ToolRegistry, pipeline: ToolPipeline) -> None:
        self.s = services
        self.registry = registry
        self.pipeline = pipeline
        self.planner = Planner(services)

    async def run(self, task: TaskState, ctx: ToolContext, ctxb: ContextBuilder, budget: BudgetTracker, *,
                  plan_first: bool, role: str = "executor") -> ExecOutcome:
        cats = categories_for(task.objective)
        limit = 60 if self.s.router.cloud_available("reasoning") else 24
        tools = self.registry.select(cats, role=role, limit=limit)
        schemas = self.registry.function_schemas(tools)
        names = {t.name for t in tools}
        wire = {wire_name(t.name) for t in tools} | names
        if plan_first and task.plan is None:
            task.status = TaskStatus.PLANNING
            task.plan = await self.planner.plan(task, sorted(names))
            task.status = TaskStatus.RUNNING
        malformed_streak = 0
        replans = 0
        warned_repeat: set[str] = set()
        actions_taken = 0
        while True:
            ctx.cancel.raise_if_cancelled()
            budget.step()
            task.budgets_used.steps = budget.steps
            self.s.bus.publish(StepStarted(task_id=task.task_id, step=budget.steps, message=""))
            local_route = self.s.router.last_route.get("reasoning", "").startswith(("ollama", "llamacpp"))
            if local_route and ctxb.budget_tokens > LOCAL_BUDGET_TOKENS:
                ctxb.budget_tokens = LOCAL_BUDGET_TOKENS
            req = ctxb.request(schemas, max_tokens=2048)
            try:
                resp = await self.s.router.chat("reasoning", req, task=task, compactor=ctxb.compact_for_overflow,
                                                data_classes=self._data_classes(task))
            except AllProvidersFailed as exc:
                if len(schemas) <= 14 or not any(a.kind == ProviderErrorKind.CONTEXT_OVERFLOW for a in exc.attempts):
                    raise
                # too big for every reachable model: offer fewer tools and a tighter context, once
                tools = [t for t in tools if t.internal] + [t for t in tools if not t.internal][:12]
                schemas = self.registry.function_schemas(tools)
                names = {t.name for t in tools}
                wire = {wire_name(t.name) for t in tools} | names
                ctxb.budget_tokens = max(3000, ctxb.budget_tokens // 2)
                req = ctxb.request(schemas, max_tokens=1536)
                resp = await self.s.router.chat("reasoning", req, task=task, data_classes=self._data_classes(task))
            budget.add_tokens(resp.usage.prompt_tokens + resp.usage.completion_tokens)
            calls = resp.tool_calls
            if not calls and resp.content:
                calls = parse_json_action(resp.content, wire)
            if not calls:
                text = resp.content.strip()
                if not text:
                    malformed_streak += 1
                    if malformed_streak >= 2:
                        self._penalise(resp.provider, resp.model, "empty responses")
                    ctxb.add_note("Your last reply was empty. Continue: call a tool or finish.")
                    continue
                # plain answer: conversational reply, or a final summary without calling finish
                ctxb.add_assistant_text(text)
                if actions_taken == 0:
                    return ExecOutcome(TaskStatus.SUCCEEDED, text, None)
                v = verify_finish(task, text, [], self.s, self.registry)
                last = next((o for o in reversed(task.observations) if o.tool not in ("ask_user", "read_artifact")), None)
                last_failed = last is not None and last.result.status != ToolStatus.OK
                ok = v.verified is not False and not last_failed
                return ExecOutcome(TaskStatus.SUCCEEDED if ok else TaskStatus.FAILED, honest_summary(text, v), v)
            ctxb.add_assistant_calls(resp.content, calls)
            bad_calls = 0
            for call in calls[:6]:
                tool = self.registry.by_wire_name(call.name) or self.registry.get(call.name)
                if call.parse_error or tool is None or tool.name not in names:
                    bad_calls += 1
                    reason = (f"invalid JSON arguments ({call.parse_error}); send a valid JSON object" if call.parse_error
                              else f"unknown tool {call.name!r}; use one of the provided tools")
                    ctxb.add_tool_result(call, None, f"[error] {reason}")
                    continue
                if tool.name == "finish":
                    args = call.arguments
                    summary = str(args.get("summary", "")).strip() or "Done."
                    status = str(args.get("status", "done"))
                    evidence = args.get("evidence") or []
                    task.status = TaskStatus.VERIFYING
                    v = verify_finish(task, summary, evidence if isinstance(evidence, list) else [], self.s, self.registry)
                    final = honest_summary(summary, v)
                    if status == "failed":
                        return ExecOutcome(TaskStatus.FAILED, final, v)
                    if v.verified is False and replans < 1 and budget.steps < budget.budget.max_steps - 3:
                        failed = [c.name for c in v.checks if not c.passed]
                        ctxb.add_tool_result(call, None, f"[verification failed] {', '.join(failed)}. The objective is not "
                                                         "complete yet. Fix it, or finish with status 'failed' and explain.")
                        replans += 1
                        task.status = TaskStatus.RUNNING
                        continue
                    return ExecOutcome(TaskStatus.SUCCEEDED if v.verified is not False else TaskStatus.FAILED, final, v)
                obs = await self._execute(tool.name, call, ctx, task)
                actions_taken += 1
                ctxb.add_tool_result(call, obs)
                fp = f"{tool.name}:{hash_args(tool.name, call.arguments)[:12]}:{obs.result.status.value}"
                budget.record_state(fp)
                if obs.result.status in (ToolStatus.ERROR, ToolStatus.TIMEOUT):
                    key = hash_args(tool.name, call.arguments)
                    n = budget.record_failure(key)
                    if budget.repeated_failure(key):
                        if key in warned_repeat:
                            if replans >= 2:
                                return self._abort(task, f"the same action kept failing: {obs.result.summary}")
                            replans += 1
                            await self._replan(task, ctxb, names, obs.result.summary)
                        else:
                            warned_repeat.add(key)
                            ctxb.add_note(f"{tool.name} failed {n} times with the same arguments. Do not repeat it; use a "
                                          "different mechanism (UIA → OCR/vision → keyboard), different arguments, or ask "
                                          "the user.")
                elif obs.result.status == ToolStatus.DENIED:
                    ctxb.add_note("That action was not permitted. Do not try to achieve the same effect another way; "
                                  "continue with what is allowed or finish and tell the user what was blocked.")
                elif obs.result.status == ToolStatus.UNAVAILABLE:
                    ctxb.add_note("That capability is unavailable on this system. Use an alternative, or finish and tell "
                                  f"the user what is missing ({obs.result.missing_prerequisite}).")
                elif obs.result.status == ToolStatus.CANCELLED:
                    raise Cancelled(obs.result.summary)
                self._advance_plan(task, tool.name, obs)
            if bad_calls:
                malformed_streak += 1
                if malformed_streak >= 2:
                    self._penalise(resp.provider, resp.model, "invalid tool calls")
                    malformed_streak = 0
            else:
                malformed_streak = 0
            if budget.no_progress():
                return self._abort(task, "no progress in the last several steps")

    async def _execute(self, tool_name: str, call: ToolCall, ctx: ToolContext, task: TaskState) -> Observation:
        obs = await self.pipeline.execute(tool_name, call.arguments, ctx, requested_by=ctx.role, call_id=call.id)
        return obs

    async def _replan(self, task: TaskState, ctxb: ContextBuilder, names: set[str], failure: str) -> None:
        plan = await self.planner.revise(task, failure, sorted(names))
        if plan is not None:
            task.plan = plan
            ctxb.add_note("The plan was revised after repeated failures:\n" + "\n".join(f"{s.index}. {s.intent}" for s in plan.steps))
        else:
            ctxb.add_note("Change approach now; if nothing works, ask the user or finish with status 'failed'.")

    def _advance_plan(self, task: TaskState, tool_name: str, obs: Observation) -> None:
        if task.plan is None or obs.result.status != ToolStatus.OK:
            return
        for step in task.plan.steps:
            if not step.done:
                matches = not step.candidate_tools or tool_name in step.candidate_tools
                ok = obs.result.verification is None or obs.result.verification.verified is not False
                if matches and ok:
                    step.done = True
                    self.s.bus.publish(TaskProgress(task_id=task.task_id, message=obs.result.summary))
                break

    def _penalise(self, provider: str, model: str, why: str) -> None:
        if provider:
            self.s.health.failure(ProviderError(ProviderErrorKind.MALFORMED, why, provider=provider, model=model))

    def _abort(self, task: TaskState, reason: str) -> ExecOutcome:
        done = [o for o in task.observations if o.result.status == ToolStatus.OK]
        failed = [o for o in task.observations if o.result.status not in (ToolStatus.OK,)]
        parts = [f"I stopped: {reason}."]
        if done:
            parts.append("Done: " + "; ".join(o.result.summary for o in done[-4:]) + ".")
        if failed:
            parts.append("Failed: " + "; ".join(f"{o.result.summary}" for o in failed[-3:]) + ".")
        return ExecOutcome(TaskStatus.FAILED, " ".join(parts), VerificationResult(verified=False, note=reason))

    @staticmethod
    def _data_classes(task: TaskState) -> list[str]:
        classes = {"general"}
        for o in task.observations[-20:]:
            if o.tool.startswith(("email.",)):
                classes.add("email")
            elif o.tool.startswith(("message.",)):
                classes.add("messages")
            elif o.tool.startswith(("screen.", "vision.", "uia.")):
                classes.add("screen")
            elif o.tool.startswith(("fs.read", "documents.read", "fs.search", "fs.diff")):
                classes.add("files")
            elif o.tool.startswith("clipboard."):
                classes.add("clipboard")
            elif o.tool.startswith("contacts."):
                classes.add("contacts")
        return sorted(classes)


def budget_exceeded_summary(task: TaskState, exc: BudgetExceeded) -> str:
    done = [o.result.summary for o in task.observations if o.result.status == ToolStatus.OK][-4:]
    return f"I ran out of {exc.budget.replace('_', ' ')} before finishing." + (f" Done so far: {'; '.join(done)}." if done else "")


def compact_json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))[:2000]


def provider_unavailable_message(exc: AllProvidersFailed) -> str:
    reasons = sorted({a.kind.value for a in exc.attempts})
    if "context_overflow" in reasons and not any(a.kind.value in ("rate_limit", "transient", "quota") for a in exc.attempts):
        return ("That request was too large for the language model available right now (its context window is too "
                "small). Try a narrower request, or configure a cloud provider with a larger context (see `scar doctor`).")
    return ("I can't reason about that right now — no language model is reachable "
            f"({', '.join(reasons) or 'none configured'}). I can still do quick commands like opening apps or folders, "
            "screenshots, system info, reminders and memory. Run `scar doctor` to see how to fix this.")
