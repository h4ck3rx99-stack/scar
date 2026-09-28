"""Task-level verification (C8.5): `finish` must carry evidence, checked deterministically where possible."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import psutil

from scar.core.types import Check, SideEffect, TaskState, ToolStatus, TrustLevel, VerificationResult
from scar.tools.fs.common import sha256_file
from scar.tools.web.citations import check_citations, fetched_urls


def _obs_data(task: TaskState, tool_prefix: str) -> list[dict[str, Any]]:
    return [o.result.data for o in task.observations if o.tool.startswith(tool_prefix) and o.result.status == ToolStatus.OK]


def _fetch_log(services: Any, task_id: str) -> list[str]:
    logs: dict[str, list[str]] = services.extras.get("fetch_log") or {}
    return list(logs.get(task_id) or [])


_MODIFY = re.compile(r"\b(fix|fixes|fixing|repair|correct|patch|edit|modify|refactor|rename)\b", re.I)
_CODE_OBJECT = re.compile(r"\b(tests?|bugs?|code|files?|functions?|scripts?|modules?|project|repo(sitory)?|class|method)\b|\.\w{1,5}\b",
                          re.I)
_TESTS = re.compile(r"\btests?\b", re.I)
_RERUN = re.compile(r"\b(re-?run|again|make (them|it|the tests?) pass|until (they|it|the tests?) pass)\b", re.I)
_NEGATED = re.compile(r"\b(don'?t|do not|without|no need to|never)\s+(\w+\s+){0,2}(fix|edit|modify|change|touch)", re.I)
# imperative file actions ("move a.txt to b.txt", "please delete the old logs folder")
_FILE_ACTION = re.compile(r"^(please\s+|can you\s+|could you\s+)?(move|rename|copy|delete|remove|trash|create|make|write|save)\b",
                          re.I)
_FILE_OBJECT = re.compile(r"\b(files?|folders?|director(y|ies)|documents?)\b|\.[a-z0-9]{2,5}\b|[a-z]:\\", re.I)
_TERMINAL = frozenset({"terminal.run", "terminal.exec", "code.run"})
_FILE_ACTION_TOOLS: list[tuple[set[str], frozenset[str]]] = [
    ({"move", "rename"}, frozenset({"fs.move"}) | _TERMINAL),
    ({"copy"}, frozenset({"fs.copy"}) | _TERMINAL),
    ({"delete", "remove", "trash"}, frozenset({"fs.delete"}) | _TERMINAL),
    ({"create", "make", "write", "save"}, frozenset({"fs.write", "fs.mkdir", "fs.edit", "fs.copy", "documents.write"}) | _TERMINAL),
]
FILE_CHANGE_TOOLS = frozenset({"fs.write", "fs.edit", "fs.move", "fs.copy", "documents.write", "terminal.run", "terminal.exec",
                               "code.run"})


def objective_checks(task: TaskState) -> list[Check]:
    """What the user's words require, checked against what actually happened (not what the model claims).

    "fix the failing test and run the tests again" needs a change to files and a passing test run after it; a
    diagnosis alone is not success."""
    objective = task.objective
    ok = [o for o in task.observations if o.result.status == ToolStatus.OK]
    checks: list[Check] = []
    act = _FILE_ACTION.search(objective)
    if act and _FILE_OBJECT.search(objective) and not _NEGATED.search(objective):
        verb = act.group(2).lower()
        wanted = next((tools for verbs, tools in _FILE_ACTION_TOOLS if verb in verbs), FILE_CHANGE_TOOLS)
        done = any(o.tool in wanted for o in ok)
        checks.append(Check(name="requested change was made", passed=done,
                            detail="" if done else f"nothing was {verb}d" if verb.endswith("e") else f"nothing was {verb}ed"))
    if _MODIFY.search(objective) and _CODE_OBJECT.search(objective) and not _NEGATED.search(objective):
        changed = [i for i, o in enumerate(ok) if o.tool in FILE_CHANGE_TOOLS]
        checks.append(Check(name="requested change was made", passed=bool(changed),
                            detail="" if changed else "no file was modified"))
        if _TESTS.search(objective):
            last_change = changed[-1] if changed else -1
            runs = [o for i, o in enumerate(ok) if o.tool == "dev.run_tests" and i > last_change]
            if runs or _RERUN.search(objective):
                report = (runs[-1].result.data or {}).get("report") or {} if runs else {}
                failing = int(report.get("failed", 0) or 0) + int(report.get("errors", 0) or 0)
                checks.append(Check(name="tests pass after the change", passed=bool(runs) and failing == 0,
                                    detail="tests were not re-run after the change" if not runs else f"{failing} still failing"))
    return checks


def verify_finish(task: TaskState, summary: str, evidence: list[dict[str, Any]], services: Any, registry: Any) -> VerificationResult:
    checks: list[Check] = objective_checks(task)
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
        # URLs SCAR itself produced in structured results (e.g. a dev server's address) are facts, not citations
        for o in task.observations:
            if o.result.status == ToolStatus.OK and o.result.provenance.trust != TrustLevel.UNTRUSTED_EXTERNAL:
                allowed |= {u.lower().rstrip("/.,;:") for u in re.findall(r"https?://[^\s\"'<>)]+", json.dumps(o.result.data, default=str))}
        _ok, bad = check_citations(summary, {a.replace("http://", "https://").replace("://www.", "://") for a in allowed})
        checks.append(Check(name="summary cites only fetched URLs", passed=not bad, detail=", ".join(bad[:3])))
    # automatic evidence: verified side-effecting actions in this task count as deterministic checks
    for o in task.observations:
        v = o.result.verification
        if v is not None and v.verified is True and o.result.status == ToolStatus.OK:
            checks.append(Check(name=f"{o.tool}: {o.result.summary[:60]}", passed=True))
    ev["unverified_actions"] = unverified_actions
    ev["side_effects"] = any(
        o.result.status == ToolStatus.OK and (t := registry.get(o.tool) if registry is not None else None) is not None
        and t.side_effects != SideEffect.NONE and not t.internal for o in task.observations)
    ev["failed_postconditions"] = failed_actions
    if failed_actions:
        checks.append(Check(name="no failed postconditions", passed=False, detail=", ".join(failed_actions[:4])))
    result = VerificationResult.from_checks(checks, ev)
    if result.verified is True and unverified_actions:
        # some effects could not be confirmed: never round up
        result.note = f"could not confirm: {', '.join(sorted(set(unverified_actions)))}"
    return result


def honest_summary(summary: str, verification: VerificationResult | None) -> str:
    if verification is not None and verification.verified is None and verification.evidence.get("side_effects") is False:
        return summary  # read-only task: the answer comes straight from tool results, nothing to confirm
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
