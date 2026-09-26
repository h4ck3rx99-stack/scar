"""Terminal rendering (C11/C12): short progress lines, final replies, inline approval and clarification prompts.

Works on plain event dicts so the embedded runtime and daemon-attached sessions share it.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scar.security.redaction import global_redactor

console = Console(highlight=False, soft_wrap=False)

RESPONSES = [
    ("1", "allow_once", "Allow once"),
    ("2", "allow_task", "Allow for this task"),
    ("3", "allow_session", "Allow for this session"),
    ("4", "allow_timed", "Allow for 60 minutes"),
    ("5", "allow_always", "Always allow this exact pattern"),
    ("6", "deny", "Deny"),
    ("7", "deny_always", "Always deny"),
]

_RISK_STYLE = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "bold red", "CRITICAL": "bold white on red"}


async def ainput(prompt: str, *, password: bool = False) -> str:
    """Async line input that plays well with a running event loop."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout

        session: PromptSession[str] = PromptSession()
        with patch_stdout():
            return await session.prompt_async(prompt, is_password=password)
    except Exception:  # noqa: BLE001 - no usable console for prompt_toolkit: plain input
        if password:
            import getpass

            return await asyncio.to_thread(getpass.getpass, prompt)
        return await asyncio.to_thread(input, prompt)


class Renderer:
    def __init__(self, *, verbose: bool = False, debug: bool = False, interactive: bool | None = None) -> None:
        self.verbose = verbose or debug
        self.debug = debug
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self._last_progress = ""

    # ---------------------------------------------------------------- events
    def event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("kind")
        msg = global_redactor().redact(str(ev.get("message") or ""))
        if kind == "task_progress":
            if (ev.get("milestone", True) or self.verbose) and msg and msg != self._last_progress:
                self._last_progress = msg
                console.print(Text("  " + msg, style="dim" if not ev.get("milestone", True) else "cyan"))
        elif kind == "tool_called" and self.verbose:
            console.print(Text(f"  → {ev.get('tool')} [{ev.get('risk')}] {msg}", style="dim"))
        elif kind == "tool_completed" and self.verbose:
            v = ev.get("verified")
            mark = {True: "✓", False: "✗", None: "·"}[v]
            console.print(Text(f"  {mark} {ev.get('tool')} {ev.get('status')} ({ev.get('duration_ms')} ms) {msg}", style="dim"))
        elif kind == "provider_fallback" and self.debug:
            console.print(Text(f"  ⇄ {ev.get('category')}: {ev.get('from_provider')} → {ev.get('to_provider')} "
                               f"({ev.get('reason')})", style="dim magenta"))
        elif kind == "plan_created" and self.verbose:
            for i, step in enumerate(ev.get("steps") or [], 1):
                console.print(Text(f"  {i}. {step}", style="dim"))
        elif kind in ("monitor_fired", "reminder_due"):
            console.print(Text(f"🔔 {msg}", style="bold yellow"))
        elif kind == "assistant_message" and msg and ev.get("task_id") is None:
            console.print(Text(msg, style="bold"))

    def result(self, status: str, summary: str, verified: bool | None = None) -> None:
        style = {"succeeded": "bold green", "failed": "bold red", "cancelled": "yellow"}.get(status, "bold")
        text = global_redactor().redact(summary)
        console.print(Text(text, style=style if status != "succeeded" else "bold"))
        if self.verbose:
            console.print(Text(f"  [{status}; verified={verified}]", style="dim"))

    # ---------------------------------------------------------------- approvals
    async def ask_approval(self, req: dict[str, Any]) -> tuple[str, str | None]:
        """Returns (response, typed_confirmation)."""
        risk = str(req.get("risk", "")).upper() if not isinstance(req.get("risk"), int) else \
            {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}[int(req["risk"])]
        table = Table.grid(padding=(0, 1))
        table.add_row(Text("Action", style="bold"), Text(global_redactor().redact(str(req.get("summary", "")))))
        table.add_row(Text("Risk", style="bold"), Text(risk, style=_RISK_STYLE.get(risk, "")))
        table.add_row(Text("Why", style="bold"), Text(str(req.get("reason", ""))))
        details = req.get("details") or {}
        for key in ("to", "cc", "recipient", "recipients", "subject", "body", "attachments", "paths", "file_count", "command",
                    "cwd", "shell", "diff", "code", "url"):
            if key in details and details[key] not in (None, "", []):
                val = details[key]
                if isinstance(val, list):
                    val = "\n".join(str(v) for v in val[:20])
                text = global_redactor().redact(str(val))
                table.add_row(Text(key, style="bold"), Text(text[:3000]))
        console.print(Panel(table, title="Approval needed", border_style=_RISK_STYLE.get(risk, "yellow")))
        if not self.interactive:
            console.print(Text("  No interactive terminal: denied.", style="red"))
            return "deny", None
        allowed = set(req.get("allowed_responses") or [r[1] for r in RESPONSES])
        if req.get("critical"):
            code = str(req.get("confirmation_code", ""))
            answer = (await ainput(f"Type {code} to allow once, anything else denies: ")).strip()
            return ("allow_once", answer) if answer.upper() == code else ("deny", None)
        options = [(k, v, label) for k, v, label in RESPONSES if v in allowed]
        console.print("  " + "   ".join(f"[{k}] {label}" for k, _v, label in options))
        while True:
            answer = (await ainput("Choose (default 6 = deny): ")).strip().lower()
            if not answer:
                return "deny", None
            if answer in ("y", "yes", "a", "allow"):
                return "allow_once", None
            if answer in ("n", "no", "d", "deny"):
                return "deny", None
            for k, v, _label in options:
                if answer == k:
                    return v, None
            console.print("  Please pick one of the numbers.")

    async def ask_question(self, text: str, options: list[str]) -> str:
        console.print(Text(f"? {text}", style="bold cyan"))
        for i, opt in enumerate(options, 1):
            console.print(f"  [{i}] {opt}")
        if not self.interactive:
            return ""
        answer = (await ainput("> ")).strip()
        if answer.isdigit() and options and 0 < int(answer) <= len(options):
            return options[int(answer) - 1]
        return answer


def check_line(ok: bool | None, label: str, hint: str = "") -> None:
    mark, style = {True: ("✓", "green"), False: ("✗", "red"), None: ("⚠", "yellow")}[ok]
    line = Text(f"{mark} ", style=style)
    line.append(label)
    if hint:
        line.append(f"  — {hint}", style="dim")
    console.print(line)
