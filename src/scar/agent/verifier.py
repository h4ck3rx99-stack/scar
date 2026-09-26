"""Task-level verification (C8.5): `finish` must carry evidence, checked deterministically where possible."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import psutil

from scar.core.types import Check, SideEffect, TaskState, ToolStatus, VerificationResult
from scar.tools.fs.common import sha256_file
from scar.tools.web.citations import check_citations, fetched_urls


def _obs_data(task: TaskState, tool_prefix: str) -> list[dict[str, Any]]:
    return [o.result.data for o in task.observations if o.tool.startswith(tool_prefix) and o.result.status == ToolStatus.OK]


def _fetch_log(services: Any, task_id: str) -> list[str]:
    logs: dict[str, list[str]] = services.extras.get("fetch_log") or {}
    return list(logs.get(task_id) or [])


def verify_finish(task: TaskState, summary: str, evidence: list[dict[str, Any]], services: Any, registry: Any) -> VerificationResult:
    checks: list[Check] = []
    ev: dict[str, Any] = {}
    for item in evidence:
        kind = item.get("kind")
        d = item.get("detail") or {}
        if kind == "file" and d.get("path"):
            p = Path(str(d["path"]))
            checks.append(Check(name=f"file {p.name} exists", passed=p.exists()))
            if d.get("sha256") and p.is_file():
                checks.append(Check(name=f"file {p.name} hash", passed=sha256_file(p) == d["sha256"]))
            if d.get("contains") and p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    checks.append(Check(name=f"file {p.name} content", passed=str(d["contains"]) in text))
                except OSError:
                    checks.append(Check(name=f"file {p.name} content", passed=False))
        elif kind == "process":
            pid = d.get("pid")
            name = str(d.get("name") or "").lower()
            if pid:
                checks.append(Check(name=f"process {pid} running", passed=psutil.pid_exists(int(pid))))
            elif name:
                running = any((p.info["name"] or "").lower() == name for p in psutil.process_iter(["name"]))
                checks.append(Check(name=f"process {name} running", passed=running))
        elif kind == "window" and (d.get("title") or d.get("process")):
            try:
                from scar.tools.windows.win32 import find_windows

                found = find_windows(title=d.get("title"), process=d.get("process"))
                checks.append(Check(name=f"window '{d.get('title') or d.get('process')}' present", passed=bool(found)))
            except (ImportError, OSError):
                pass
        elif kind == "url" and d.get("url"):
            allowed = fetched_urls(services.db, task.task_id, _fetch_log(services, task.task_id))
            opened = [o["url"] for o in _obs_data(task, "browser.") if isinstance(o.get("url"), str)]
            ok, _bad = check_citations(str(d["url"]), allowed | {u.lower().rstrip("/") for u in opened})
            checks.append(Check(name=f"URL {d['url']} was loaded", passed=bool(ok) or str(d["url"]) in opened))
        elif kind == "tests":
            reports: list[dict[str, Any]] = [r for o in _obs_data(task, "dev.run_tests") if isinstance(r := o.get("report"), dict)]
            if reports:
                last = reports[-1]
                if "failed" in d:
                    checks.append(Check(name="test failures match last run",
                                        passed=int(d.get("failed", -1)) == int(last.get("failed", 0)) + int(last.get("errors", 0))))
                if "passed" in d:
                    checks.append(Check(name="passing count matches last run", passed=int(d.get("passed", -1)) == int(last.get("passed", 0))))
                ev["last_test_report"] = last
        elif kind == "message" and d.get("message_id"):
            ids = {str(o.get("message_id")) for o in _obs_data(task, "") if o.get("message_id")}
            checks.append(Check(name="message id came from a send result", passed=str(d["message_id"]) in ids))
        elif kind == "command" and "exit_code" in d:
            codes = [o.get("exit_code") for o in _obs_data(task, "terminal.") + _obs_data(task, "dev.")]
            checks.append(Check(name=f"a command exited with {d['exit_code']}", passed=d["exit_code"] in codes))
    # every side-effecting action must not have a failed postcondition
    failed_actions = []
    unverified_actions = []
    for o in task.observations:
        tool = registry.get(o.tool) if registry is not None else None
        if tool is None or tool.side_effects == SideEffect.NONE or tool.internal:
            continue
        if o.result.status != ToolStatus.OK:
            continue
        v = o.result.verification
        if v is not None and v.verified is False:
            failed_actions.append(o.tool)
        elif v is None or v.verified is None:
            unverified_actions.append(o.tool)
    # citations in the final summary must have been fetched
    urls = re.findall(r"https?://\S+", summary)
    if urls:
        allowed = fetched_urls(services.db, task.task_id, _fetch_log(services, task.task_id))
        _ok, bad = check_citations(summary, allowed)
        checks.append(Check(name="summary cites only fetched URLs", passed=not bad, detail=", ".join(bad[:3])))
    # automatic evidence: verified side-effecting actions in this task count as deterministic checks
    for o in task.observations:
        v = o.result.verification
        if v is not None and v.verified is True and o.result.status == ToolStatus.OK:
            checks.append(Check(name=f"{o.tool}: {o.result.summary[:60]}", passed=True))
    ev["unverified_actions"] = unverified_actions
    ev["failed_postconditions"] = failed_actions
    if failed_actions:
        checks.append(Check(name="no failed postconditions", passed=False, detail=", ".join(failed_actions[:4])))
    result = VerificationResult.from_checks(checks, ev)
    if result.verified is True and unverified_actions:
        # some effects could not be confirmed: never round up
        result.note = f"could not confirm: {', '.join(sorted(set(unverified_actions)))}"
    return result


def honest_summary(summary: str, verification: VerificationResult | None) -> str:
    if verification is None or verification.verified is None:
        if "couldn't confirm" in summary.lower() or "could not confirm" in summary.lower():
            return summary
        return f"{summary.rstrip('.')}." + " (I couldn't verify this automatically.)" if summary else "Done, but unverified."
    if verification.verified is False:
        failed = [c.name for c in verification.checks if not c.passed]
        return f"{summary.rstrip('.')}. However, verification failed: {', '.join(failed[:3])}."
    if verification.note.startswith("could not confirm"):
        return f"{summary.rstrip('.')}. ({verification.note.replace('could not confirm', 'Not independently confirmed')})"
    return summary


def path_exists(path: str) -> bool:
    return os.path.exists(path)
