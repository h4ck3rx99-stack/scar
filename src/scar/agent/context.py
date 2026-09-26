"""Context construction, token budgeting and compaction (C8.4, C8.9)."""

from __future__ import annotations

import json
import platform
from datetime import datetime
from typing import Any

from scar.agent.prompts import load
from scar.core.types import Observation, Provenance, TaskState, ToolStatus
from scar.providers.base import ChatMessage, ChatRequest, ToolCall
from scar.security.injection import UNTRUSTED_RULE, wrap_untrusted

CHARS_PER_TOKEN = 4
DEFAULT_BUDGET_TOKENS = 24_000
LOCAL_BUDGET_TOKENS = 11_000
KEEP_RECENT_FULL = 6


def environment_block(services: Any) -> str:
    lines = [
        f"- Date/time: {datetime.now().astimezone().strftime('%A %Y-%m-%d %H:%M %Z')}",
        f"- OS: {platform.system()} {platform.release()} (build {platform.version()})",
        f"- User home: {__import__('pathlib').Path.home()}",
    ]
    settings = getattr(services, "settings", None)
    if settings is not None:
        roots = ", ".join(str(r) for r in settings.effective_allowed_roots[:4])
        lines.append(f"- Folders SCAR may work in: {roots}")
        lines.append(f"- Downloads folder: {settings.downloads_path}")
    cwd = getattr(services, "extras", {}).get("cwd") if services is not None else None
    if cwd:
        lines.append(f"- Current folder: {cwd}")
    try:
        from scar.tools.windows.win32 import list_monitors

        mons = list_monitors()
        lines.append("- Monitors: " + "; ".join(f"#{m.index} {m.right - m.left}x{m.bottom - m.top} at ({m.left},{m.top})"
                                                  f"{' primary' if m.primary else ''}" for m in mons))
    except (ImportError, OSError, AttributeError):
        pass
    return "\n".join(lines)


def system_prompt(services: Any, role_note: str = "") -> str:
    base = load("system").format(untrusted_rule=UNTRUSTED_RULE, environment=environment_block(services))
    return base + (f"\n\n## Your role\n{role_note}" if role_note else "")


def role_prompt(role: str) -> str:
    text = load("roles")
    marker = f"## {role}"
    if marker not in text:
        return ""
    body = text.split(marker, 1)[1]
    return body.split("\n## ", 1)[0].strip()


class ContextBuilder:
    """Maintains the model-visible transcript for one task and keeps it within a token budget."""

    def __init__(self, services: Any, task: TaskState, *, history: list[ChatMessage] | None = None,
                 memory_notes: list[str] | None = None, role_note: str = "", budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> None:
        self.services = services
        self.task = task
        self.budget_tokens = budget_tokens
        self.system = system_prompt(services, role_note)
        self.history = history or []
        self.memory_notes = memory_notes or []
        self.turns: list[ChatMessage] = []  # assistant tool calls + tool results + runtime notes
        self._obs_index: dict[str, Observation] = {}

    # ---------------------------------------------------------------- assembly
    def _preamble(self) -> list[ChatMessage]:
        msgs = [ChatMessage(role="system", content=self.system)]
        msgs.extend(self.history[-8:])
        user = f"Objective: {self.task.objective}"
        if self.memory_notes:
            user += "\n\nRelevant things you know (from memory):\n" + "\n".join(f"- {n}" for n in self.memory_notes[:8])
        if self.task.plan and self.task.plan.steps:
            plan = "\n".join(f"{s.index}. {'[done] ' if s.done else ''}{s.intent} — success: {s.success_criteria}"
                             for s in self.task.plan.steps)
            user += f"\n\nPlan (revision {self.task.plan.revision}):\n{plan}\nCompletion: {self.task.plan.completion_criteria}"
        msgs.append(ChatMessage(role="user", content=user))
        return msgs

    def messages(self) -> list[ChatMessage]:
        self._compact()
        return self._preamble() + self.turns

    def request(self, tools: list[dict[str, Any]], max_tokens: int = 2048) -> ChatRequest:
        return ChatRequest(messages=self.messages(), tools=tools, tool_choice="auto", max_tokens=max_tokens, temperature=0.2)

    # ---------------------------------------------------------------- recording
    def add_assistant_calls(self, content: str, calls: list[ToolCall]) -> None:
        self.turns.append(ChatMessage(role="assistant", content=content, tool_calls=calls))

    def add_tool_result(self, call: ToolCall, obs: Observation | None, text: str | None = None) -> None:
        if obs is not None:
            self._obs_index[call.id] = obs
            r = obs.result
            header = f"[{r.status.value}] {r.summary}"
            if r.verification is not None:
                v = r.verification
                verdict = {True: "verified", False: "VERIFICATION FAILED", None: "not verifiable"}[v.verified]
                failed = [c.name for c in v.checks if not c.passed]
                header += f"\n[postcondition: {verdict}{': ' + ', '.join(failed) if failed else ''}{' — ' + v.note if v.note else ''}]"
            if r.status == ToolStatus.UNAVAILABLE and r.missing_prerequisite:
                header += f"\n[missing prerequisite: {r.missing_prerequisite}; setup: {r.setup_doc}]"
            body = r.model_view if r.model_view and r.model_view != r.summary else ""
            content = header + ("\n" + body if body else "")
        else:
            content = text or ""
        self.turns.append(ChatMessage(role="tool", content=content, tool_call_id=call.id, name=call.name))

    def add_note(self, text: str) -> None:
        """Runtime guidance (not user intent, not external data)."""
        self.turns.append(ChatMessage(role="user", content=f"[SCAR runtime] {text}"))

    def add_assistant_text(self, text: str) -> None:
        self.turns.append(ChatMessage(role="assistant", content=text))

    # ---------------------------------------------------------------- budgeting
    def tokens(self) -> int:
        total = len(self.system) + sum(len(m.content) for m in self.history[-8:])
        total += sum(len(m.content) + sum(len(tc.raw_arguments or json.dumps(tc.arguments)) for tc in m.tool_calls)
                     for m in self.turns)
        return total // CHARS_PER_TOKEN + 500

    def _compact(self) -> None:
        if self.tokens() <= self.budget_tokens:
            return
        # 1. shorten old tool results to their first lines (the full text stays in artifacts / the trace)
        tool_idx = [i for i, m in enumerate(self.turns) if m.role == "tool"]
        for i in tool_idx[:-KEEP_RECENT_FULL]:
            m = self.turns[i]
            if len(m.content) > 400:
                first = m.content.splitlines()[:3]
                self.turns[i] = m.model_copy(update={"content": "\n".join(first)[:400] + "\n[older result compacted]"})
            if self.tokens() <= self.budget_tokens:
                return
        # 2. collapse the oldest exchanges into a summary note
        while self.tokens() > self.budget_tokens and len(self.turns) > KEEP_RECENT_FULL * 2:
            cut = next((i for i in range(2, len(self.turns)) if self.turns[i].role == "assistant"), len(self.turns) // 2)
            dropped = self.turns[:cut]
            summary = "; ".join(
                f"{tc.name}" for m in dropped for tc in m.tool_calls)[:600]
            outcomes = "; ".join(m.content.splitlines()[0][:100] for m in dropped if m.role == "tool")[:900]
            note = ChatMessage(role="user", content=f"[SCAR runtime] Earlier steps (compacted): calls {summary}. Outcomes: {outcomes}")
            self.turns = [note, *self.turns[cut:]]
        # 3. still too big (huge recent result): truncate the largest recent tool message
        while self.tokens() > self.budget_tokens:
            idx = max(range(len(self.turns)), key=lambda i: len(self.turns[i].content), default=None)
            if idx is None or len(self.turns[idx].content) < 800:
                break
            m = self.turns[idx]
            self.turns[idx] = m.model_copy(update={"content": m.content[: len(m.content) // 2] + "\n[truncated to fit context]"})

    async def compact_for_overflow(self, request: ChatRequest) -> ChatRequest:
        """Called by the router on a context-overflow error: halve the budget and rebuild."""
        self.budget_tokens = max(4000, self.budget_tokens // 2)
        return request.model_copy(update={"messages": self.messages()})


def wrap_memory(note: str, trust: str) -> str:
    if trust in ("user", "runtime"):
        return note
    return wrap_untrusted(note, Provenance.external("memory"))
