"""Software-development tools (C9.10): git, project detection, tests, build/lint/format, packages, code runs, repo map."""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, ToolStatus, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.dev.project import detect_project, parse_diagnostics, parse_test_output
from scar.tools.fs.common import check_path
from scar.tools.terminal.runner import ProcessOutcome, run_process

TERMINAL = Requires(setting="terminal_enabled")


def _repo(ctx: ToolContext, raw: str | None) -> Path:
    base = raw or ctx.services.extras.get("cwd") or os.getcwd()
    p = check_path(ctx, base, PathOp.EXECUTE).path
    if not p.is_dir():
        raise ToolError(f"not a folder: {p}", "NotFound")
    return p


async def _run(ctx: ToolContext, argv: list[str], cwd: Path, timeout: float = 300.0, label: str | None = None) -> ProcessOutcome:
    exe = shutil.which(argv[0]) or argv[0]
    return await run_process([exe, *argv[1:]], services=ctx.services, cancel=ctx.cancel, cwd=str(cwd), timeout=timeout,
                             idle_timeout=min(300.0, timeout), task_id=ctx.task_id,
                             output_cap=ctx.services.settings.output_cap_bytes, stream_label=label)


def _cmd_assess(ctx: ToolContext, argv: list[str], cwd: str | None) -> RiskAssessment:
    cls = ctx.services.command_guard.classify_argv(argv, cwd=cwd)
    a = RiskAssessment(cls.risk, list(cls.reasons))
    if cls.denied:
        from scar.security.policy import deny_key_for

        a.deny("; ".join(cls.reasons), deny_key_for(cls.reasons))
    a.facts.command = " ".join(argv)
    if cwd:
        a.facts.paths.append(ctx.services.path_guard.check(cwd, PathOp.EXECUTE).canonical)
    return a


def _out(o: ProcessOutcome) -> str:
    return (o.stdout + ("\n" + o.stderr if o.stderr.strip() else "")).strip()


# ---------------------------------------------------------------- git
class GitInput(ToolInput):
    repo: str | None = Field(None, description="repository folder (default: current folder)")


class GitStatus(Tool):
    name = "git.status"
    description = "git status (branch, staged/unstaged/untracked files, ahead/behind)."
    input_model = GitInput
    capabilities = ("git.read",)
    categories = ("dev", "git")
    requires = Requires(setting="terminal_enabled", setup_doc="docs/development.md")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    def extra_availability(self) -> Any:
        from scar.tools.base import Availability

        return Availability.yes() if shutil.which("git") else Availability.no("git is not installed", "docs/troubleshooting.md")

    async def run(self, args: GitInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        o = await _run(ctx, ["git", "status", "--porcelain=v1", "-b"], repo, 60)
        if o.exit_code != 0:
            raise ToolError(_out(o) or "git status failed", "GitError")
        lines = o.stdout.splitlines()
        branch = lines[0][3:] if lines and lines[0].startswith("##") else ""
        files = [{"status": ln[:2], "path": ln[3:]} for ln in lines[1:] if ln.strip()]
        return self.ok(f"On {branch.split('...')[0] or '?'}: {len(files)} changed file(s)",
                       {"repo": str(repo), "branch": branch, "files": files}, model_view=o.stdout, source=f"git:{repo}")


class DiffInput(GitInput):
    staged: bool = False
    paths: list[str] = Field(default_factory=list)
    ref: str | None = Field(None, description="compare against this ref, e.g. HEAD~1 or main")
    stat_only: bool = False


class GitDiff(Tool):
    name = "git.diff"
    description = "Show the diff (working tree, staged, or against a ref), optionally for specific paths."
    input_model = DiffInput
    capabilities = ("git.read",)
    categories = ("dev", "git")
    requires = TERMINAL
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: DiffInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        argv = ["git", "--no-pager", "diff", "--no-color"]
        if args.staged:
            argv.append("--staged")
        if args.stat_only:
            argv.append("--stat")
        if args.ref:
            if args.ref.startswith("-"):
                raise ToolInputError("ref may not start with '-'")
            argv.append(args.ref)
        if args.paths:
            argv += ["--", *args.paths]
        o = await _run(ctx, argv, repo, 120)
        if o.exit_code not in (0, 1):
            raise ToolError(_out(o) or "git diff failed", "GitError")
        files = re.findall(r"(?m)^diff --git a/(\S+) b/", o.stdout)
        return self.ok(f"Diff touches {len(files)} file(s)" if files else "No differences", {"files": files, "diff": o.stdout,
                       "stdout_ref": o.stdout_ref}, model_view=o.stdout or "(no diff)", source=f"git:{repo}")


class LogInput(GitInput):
    n: int = Field(15, ge=1, le=200)
    path: str | None = None


class GitLog(Tool):
    name = "git.log"
    description = "Recent commits (hash, author, date, subject)."
    input_model = LogInput
    capabilities = ("git.read",)
    categories = ("dev", "git")
    requires = TERMINAL
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: LogInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        argv = ["git", "--no-pager", "log", f"-n{args.n}", "--pretty=format:%h\x1f%an\x1f%ad\x1f%s", "--date=short"]
        if args.path:
            argv += ["--", args.path]
        o = await _run(ctx, argv, repo, 60)
        if o.exit_code != 0:
            raise ToolError(_out(o) or "git log failed", "GitError")
        commits = [dict(zip(("hash", "author", "date", "subject"), ln.split("\x1f"), strict=False)) for ln in o.stdout.splitlines() if ln]
        return self.ok(f"{len(commits)} commits", {"commits": commits},
                       model_view="\n".join(f"{c['hash']} {c['date']} {c['author']}: {c['subject']}" for c in commits))


class BranchInput(GitInput):
    action: Literal["list", "create", "switch", "delete", "rename"] = "list"
    name: str | None = None
    new_name: str | None = None
    start_point: str | None = None


class GitBranch(Tool):
    name = "git.branch"
    description = "List, create, switch, rename or delete branches."
    input_model = BranchInput
    capabilities = ("git.write",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("dev", "git")
    requires = TERMINAL

    def _argv(self, args: BranchInput) -> list[str]:
        for v in (args.name, args.new_name, args.start_point):
            if v and v.startswith("-"):
                raise ToolInputError("branch names may not start with '-'")
        if args.action == "list":
            return ["git", "branch", "--list", "-vv", "--no-color"]
        if not args.name:
            raise ToolInputError(f"{args.action} needs name")
        if args.action == "create":
            return ["git", "switch", "-c", args.name, *([args.start_point] if args.start_point else [])]
        if args.action == "switch":
            return ["git", "switch", args.name]
        if args.action == "delete":
            return ["git", "branch", "-d", args.name]
        if not args.new_name:
            raise ToolInputError("rename needs new_name")
        return ["git", "branch", "-m", args.name, args.new_name]

    def assess(self, args: BranchInput, ctx: ToolContext) -> RiskAssessment:
        try:
            argv = self._argv(args)
        except ToolInputError:
            return RiskAssessment(RiskLevel.LOW)
        a = _cmd_assess(ctx, argv, args.repo)
        if args.action in ("create", "switch"):
            a.raise_to(RiskLevel.MEDIUM, "")
        return a

    def describe(self, args: BranchInput) -> str:
        return f"git branch {args.action} {args.name or ''}".strip()

    async def run(self, args: BranchInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        o = await _run(ctx, self._argv(args), repo, 60)
        if o.exit_code != 0:
            raise ToolError(_out(o) or f"git branch {args.action} failed", "GitError")
        return self.ok(f"Branch {args.action} done" if args.action != "list" else "Branches listed",
                       {"output": _out(o), "repo": str(repo)}, model_view=_out(o))

    async def verify(self, args: BranchInput, result: ToolResult, ctx: ToolContext) -> VerificationResult | None:
        if args.action in ("create", "switch"):
            repo = Path(result.data["repo"])
            o = await _run(ctx, ["git", "rev-parse", "--abbrev-ref", "HEAD"], repo, 30)
            return VerificationResult.from_checks([Check(name=f"on branch {args.name}", passed=o.stdout.strip() == args.name,
                                                         detail=o.stdout.strip())])
        return None


class CommitInput(GitInput):
    message: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list, description="files to stage first; empty = stage nothing extra")
    all: bool = Field(False, description="stage all tracked changes (git commit -a)")


class GitCommit(Tool):
    name = "git.commit"
    description = "Stage the given paths (or all tracked changes) and create a commit. Verified by the new commit hash."
    input_model = CommitInput
    capabilities = ("git.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev", "git")
    requires = TERMINAL

    def describe(self, args: CommitInput) -> str:
        what = "all tracked changes" if args.all else (", ".join(args.paths) or "staged changes")
        return f"commit {what}: '{args.message[:80]}'"

    async def run(self, args: CommitInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        before = (await _run(ctx, ["git", "rev-parse", "HEAD"], repo, 30)).stdout.strip()
        if args.paths:
            for p in args.paths:
                if p.startswith("-"):
                    raise ToolInputError("paths may not start with '-'")
            o = await _run(ctx, ["git", "add", "--", *args.paths], repo, 60)
            if o.exit_code != 0:
                raise ToolError(_out(o), "GitError")
        argv = ["git", "commit", "-m", args.message] + (["-a"] if args.all else [])
        o = await _run(ctx, argv, repo, 120)
        if o.exit_code != 0:
            raise ToolError(_out(o) or "git commit failed", "GitError")
        after = (await _run(ctx, ["git", "rev-parse", "HEAD"], repo, 30)).stdout.strip()
        return self.ok(f"Committed {after[:8]}", {"commit": after, "previous": before, "repo": str(repo), "output": _out(o)})

    async def verify(self, args: CommitInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        repo = Path(result.data["repo"])
        o = await _run(ctx, ["git", "log", "-1", "--pretty=%H%x1f%s"], repo, 30)
        h, _, subj = o.stdout.strip().partition("\x1f")
        return VerificationResult.from_checks([
            Check(name="new commit exists", passed=h == result.data["commit"] and h != result.data["previous"]),
            Check(name="message matches", passed=subj.strip() == args.message.splitlines()[0].strip()),
        ])


class StashInput(GitInput):
    action: Literal["push", "pop", "list", "apply", "drop"] = "push"
    message: str | None = None


class GitStash(Tool):
    name = "git.stash"
    description = "Stash changes (push/pop/apply/list/drop)."
    input_model = StashInput
    capabilities = ("git.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev", "git")
    requires = TERMINAL

    def assess(self, args: StashInput, ctx: ToolContext) -> RiskAssessment:
        return _cmd_assess(ctx, ["git", "stash", args.action], args.repo) if args.action != "list" else RiskAssessment(RiskLevel.LOW)

    async def run(self, args: StashInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        argv = ["git", "stash", args.action] + (["-m", args.message] if args.action == "push" and args.message else [])
        o = await _run(ctx, argv, repo, 60)
        if o.exit_code != 0:
            raise ToolError(_out(o), "GitError")
        return self.ok(f"git stash {args.action} done", {"output": _out(o)}, model_view=_out(o))


class IntegrateInput(GitInput):
    action: Literal["merge", "rebase", "pull", "fetch", "cherry-pick", "abort"]
    ref: str | None = None
    abort_what: Literal["merge", "rebase", "cherry-pick"] | None = None


class GitIntegrate(Tool):
    name = "git.integrate"
    description = "git fetch / pull / merge / rebase / cherry-pick (history-altering operations need approval), or abort one."
    input_model = IntegrateInput
    capabilities = ("git.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev", "git")
    requires = TERMINAL
    timeout = 600.0

    def _argv(self, args: IntegrateInput) -> list[str]:
        if args.ref and args.ref.startswith("-"):
            raise ToolInputError("ref may not start with '-'")
        if args.action == "abort":
            return ["git", args.abort_what or "merge", "--abort"]
        if args.action in ("merge", "rebase", "cherry-pick") and not args.ref:
            raise ToolInputError(f"{args.action} needs ref")
        return ["git", args.action, *([args.ref] if args.ref else [])]

    def assess(self, args: IntegrateInput, ctx: ToolContext) -> RiskAssessment:
        try:
            a = _cmd_assess(ctx, self._argv(args), args.repo)
        except ToolInputError:
            return RiskAssessment(RiskLevel.MEDIUM)
        if args.action in ("rebase",):
            a.raise_to(RiskLevel.HIGH, "rebase rewrites history")
        return a

    def describe(self, args: IntegrateInput) -> str:
        return " ".join(self._argv(args))

    async def run(self, args: IntegrateInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        o = await _run(ctx, self._argv(args), repo, 600, label="git")
        conflict = "CONFLICT" in o.stdout + o.stderr
        res = self.ok(f"git {args.action}: {'conflicts — resolve or abort' if conflict else 'exit ' + str(o.exit_code)}",
                      {"exit_code": o.exit_code, "conflict": conflict, "output": _out(o)}, model_view=_out(o))
        if o.exit_code != 0 and not conflict:
            res.status = ToolStatus.ERROR
            res.error_type = "GitError"
        return res


class PushInput(GitInput):
    remote: str = "origin"
    branch: str | None = None
    set_upstream: bool = False
    force: bool = Field(False, description="force-with-lease (CRITICAL)")


class GitPush(Tool):
    name = "git.push"
    description = "Push commits to a remote. HIGH risk; force push is CRITICAL."
    input_model = PushInput
    capabilities = ("git.push",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.EXTERNAL
    categories = ("dev", "git")
    requires = TERMINAL
    timeout = 600.0

    def _argv(self, args: PushInput) -> list[str]:
        for v in (args.remote, args.branch or ""):
            if v.startswith("-"):
                raise ToolInputError("remote/branch may not start with '-'")
        argv = ["git", "push"]
        if args.set_upstream:
            argv.append("-u")
        if args.force:
            argv.append("--force-with-lease")
        argv.append(args.remote)
        if args.branch:
            argv.append(args.branch)
        return argv

    def assess(self, args: PushInput, ctx: ToolContext) -> RiskAssessment:
        a = _cmd_assess(ctx, self._argv(args), args.repo)
        a.raise_to(RiskLevel.HIGH, "publishes commits to a remote")
        return a

    def describe(self, args: PushInput) -> str:
        return " ".join(self._argv(args))

    async def run(self, args: PushInput, ctx: ToolContext) -> ToolResult:
        repo = _repo(ctx, args.repo)
        o = await _run(ctx, self._argv(args), repo, 600, label="git push")
        if o.exit_code != 0:
            raise ToolError(_out(o) or "git push failed", "GitError")
        head = (await _run(ctx, ["git", "rev-parse", "HEAD"], repo, 30)).stdout.strip()
        return self.ok("Pushed", {"output": _out(o), "head": head, "repo": str(repo), "remote": args.remote})

    async def verify(self, args: PushInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        repo = Path(result.data["repo"])
        branch = args.branch or (await _run(ctx, ["git", "rev-parse", "--abbrev-ref", "HEAD"], repo, 30)).stdout.strip()
        o = await _run(ctx, ["git", "ls-remote", args.remote, f"refs/heads/{branch}"], repo, 60)
        remote_head = o.stdout.split()[0] if o.stdout.strip() else ""
        return VerificationResult.from_checks([Check(name="remote branch at local HEAD", passed=remote_head == result.data["head"],
                                                     detail=remote_head[:10])])


class CloneInput(ToolInput):
    url: str
    dest: str
    depth: int | None = Field(None, ge=1)


class GitClone(Tool):
    name = "git.clone"
    description = "Clone a repository into a folder."
    input_model = CloneInput
    capabilities = ("git.write",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev", "git")
    requires = TERMINAL
    timeout = 1200.0
    sensitive_args = {"url": "url", "dest": "path"}

    def assess(self, args: CloneInput, ctx: ToolContext) -> RiskAssessment:
        chk = ctx.services.path_guard.check(args.dest, PathOp.CREATE)
        a = RiskAssessment(max(RiskLevel.MEDIUM, chk.risk), chk.reasons if chk.risk > RiskLevel.MEDIUM else [])
        if chk.denied:
            a.deny("; ".join(chk.reasons))
        if args.url.startswith("-"):
            a.deny("invalid URL")
        from urllib.parse import urlparse

        a.facts.domains.append((urlparse(args.url).hostname or "").lower())
        return a

    async def run(self, args: CloneInput, ctx: ToolContext) -> ToolResult:
        dest = check_path(ctx, args.dest, PathOp.CREATE).path
        if dest.exists() and any(dest.iterdir()):
            raise ToolError(f"{dest} exists and is not empty", "Exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = ["git", "clone", *(["--depth", str(args.depth)] if args.depth else []), "--", args.url, str(dest)]
        o = await _run(ctx, argv, dest.parent, 1200, label="git clone")
        if o.exit_code != 0:
            raise ToolError(_out(o), "GitError")
        return self.ok(f"Cloned into {dest}", {"path": str(dest)})

    async def verify(self, args: CloneInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult.from_checks([Check(name="repository exists", passed=(Path(result.data["path"]) / ".git").exists())])


# ---------------------------------------------------------------- project tooling
class ProjectInput(ToolInput):
    path: str | None = Field(None, description="project folder (default: current folder)")


def _preferred_pm(ctx: ToolContext, root: Path) -> str | None:
    mem = ctx.services.memory
    if mem is None:
        return None
    try:
        return mem.project_preference(str(root), "package_manager")  # type: ignore[no-any-return]
    except (AttributeError, OSError):
        return None


class DevDetect(Tool):
    name = "dev.detect"
    description = "Detect a project's type, package manager and its test/build/lint/format/dev commands."
    input_model = ProjectInput
    capabilities = ("fs.read",)
    categories = ("dev",)

    async def run(self, args: ProjectInput, ctx: ToolContext) -> ToolResult:
        root = _repo(ctx, args.path)
        info = detect_project(root, _preferred_pm(ctx, root))
        return self.ok(f"{', '.join(info.kinds) or 'unknown'} project ({info.package_manager or 'no package manager'})",
                       info.as_dict())


class TestsInput(ProjectInput):
    command: list[str] | None = Field(None, description="override the detected test command (argv)")
    filter: str | None = Field(None, description="only run tests matching this (pytest -k / jest -t)")
    timeout_s: float = Field(900, gt=0, le=3600)


class DevRunTests(Tool):
    name = "dev.run_tests"
    description = "Run the project's tests and return pass/fail counts and failing test names with excerpts."
    input_model = TestsInput
    capabilities = ("terminal.exec",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev",)
    requires = TERMINAL
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 3700.0
    timeout_field = "timeout_s"
    resource_slot = "subprocess"

    def _argv(self, args: TestsInput, root: Path, ctx: ToolContext) -> list[str]:
        argv = list(args.command) if args.command else (detect_project(root, _preferred_pm(ctx, root)).test_command or [])
        if not argv:
            raise ToolError("could not determine how to run tests here; pass command", "NoTestCommand")
        if args.filter:
            if "pytest" in " ".join(argv):
                argv += ["-k", args.filter]
            elif any(x in argv[0] for x in ("npm", "pnpm", "yarn")):
                argv += ["--", "-t", args.filter]
        return argv

    def assess(self, args: TestsInput, ctx: ToolContext) -> RiskAssessment:
        root = Path(ctx.services.path_guard.check(args.path or ctx.services.extras.get("cwd") or os.getcwd(), PathOp.EXECUTE).canonical)
        try:
            argv = self._argv(args, root, ctx)
        except ToolError:
            return RiskAssessment(RiskLevel.MEDIUM)
        a = _cmd_assess(ctx, argv, str(root))
        a.raise_to(RiskLevel.MEDIUM, "")
        return a

    def describe(self, args: TestsInput) -> str:
        return f"run tests in {args.path or 'the current project'}"

    def progress_line(self, args: TestsInput) -> str | None:
        return "Running tests."

    async def run(self, args: TestsInput, ctx: ToolContext) -> ToolResult:
        root = _repo(ctx, args.path)
        argv = self._argv(args, root, ctx)
        o = await _run(ctx, argv, root, args.timeout_s, label="tests")
        text = _out(o)
        report = parse_test_output(text)
        excerpts = _failure_excerpts(text, report.failures)
        if report.parsed:
            summary = (f"{report.failed + report.errors} test(s) failed, {report.passed} passed" if report.failed or report.errors
                       else f"All {report.passed} tests passed")
        else:
            summary = f"Test command exited with {o.exit_code} (could not parse results)"
        data = {"command": argv, "cwd": str(root), "exit_code": o.exit_code, "report": report.as_dict(),
                "excerpts": excerpts, "stdout_ref": o.stdout_ref, "duration_s": o.duration_s, "notes": o.notes}
        view = f"{summary}\n" + "\n".join(f"FAILED {f['name']}: {f['message']}" for f in report.failures[:20])
        view += "\n\n" + (excerpts or text[-6000:])
        res = self.ok(summary, data, model_view=view, source=f"tests:{root}")
        if o.timed_out:
            res.status, res.error_type = ToolStatus.TIMEOUT, "Timeout"
        return res


def _failure_excerpts(text: str, failures: list[dict[str, str]], limit: int = 8000) -> str:
    """Pull the traceback sections for failing tests (pytest '____ test_x ____' blocks)."""
    blocks = re.split(r"(?m)^_{3,} (.+?) _{3,}$", text)
    out: list[str] = []
    if len(blocks) > 1:
        for i in range(1, len(blocks) - 1, 2):
            out.append(f"=== {blocks[i]} ===\n{blocks[i + 1].strip()[:2500]}")
    elif failures:
        out.append(text[-limit:])
    return "\n\n".join(out)[:limit]


class ToolingInput(ProjectInput):
    task: Literal["build", "lint", "format"]
    command: list[str] | None = None
    timeout_s: float = Field(900, gt=0, le=3600)


class DevTooling(Tool):
    name = "dev.tooling"
    description = "Run the project's build, lint or format command and parse diagnostics (file:line: message)."
    input_model = ToolingInput
    capabilities = ("terminal.exec",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev",)
    requires = TERMINAL
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 3700.0
    timeout_field = "timeout_s"
    resource_slot = "subprocess"

    def _argv(self, args: ToolingInput, root: Path, ctx: ToolContext) -> list[str]:
        if args.command:
            return list(args.command)
        info = detect_project(root, _preferred_pm(ctx, root))
        argv = {"build": info.build_command, "lint": info.lint_command, "format": info.format_command}[args.task]
        if not argv:
            raise ToolError(f"no {args.task} command detected; pass command", "NoCommand")
        return argv

    def assess(self, args: ToolingInput, ctx: ToolContext) -> RiskAssessment:
        root = Path(ctx.services.path_guard.check(args.path or ctx.services.extras.get("cwd") or os.getcwd(), PathOp.EXECUTE).canonical)
        try:
            a = _cmd_assess(ctx, self._argv(args, root, ctx), str(root))
        except ToolError:
            return RiskAssessment(RiskLevel.MEDIUM)
        a.raise_to(RiskLevel.MEDIUM, "")
        return a

    def progress_line(self, args: ToolingInput) -> str | None:
        return {"build": "Building.", "lint": "Linting.", "format": "Formatting."}[args.task]

    async def run(self, args: ToolingInput, ctx: ToolContext) -> ToolResult:
        root = _repo(ctx, args.path)
        argv = self._argv(args, root, ctx)
        o = await _run(ctx, argv, root, args.timeout_s, label=args.task)
        text = _out(o)
        diags = parse_diagnostics(text)
        summary = f"{args.task.title()} {'succeeded' if o.exit_code == 0 else 'failed'}" + (f" ({len(diags)} diagnostics)" if diags else "")
        return self.ok(summary, {"command": argv, "exit_code": o.exit_code, "diagnostics": diags, "stdout_ref": o.stdout_ref},
                       model_view=f"{summary}\n{text[-8000:]}", source=f"{args.task}:{root}")


class InstallInput(ProjectInput):
    packages: list[str] = Field(default_factory=list, description="packages to add; empty = install dependencies")
    dev: bool = False


class DevInstall(Tool):
    name = "dev.install"
    description = "Install project dependencies, or add packages, with the project's own package manager (never global)."
    input_model = InstallInput
    capabilities = ("dev.install",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("dev",)
    requires = TERMINAL
    timeout = 1800.0
    resource_slot = "subprocess"

    def _argv(self, args: InstallInput, root: Path, ctx: ToolContext) -> list[str]:
        info = detect_project(root, _preferred_pm(ctx, root))
        for p in args.packages:
            if p.startswith("-"):
                raise ToolInputError("package names may not start with '-'")
        if not args.packages:
            if not info.install_command:
                raise ToolError("no install command detected", "NoCommand")
            return info.install_command
        pm = info.package_manager
        if pm == "uv":
            return ["uv", "add", *(["--dev"] if args.dev else []), *args.packages]
        if pm == "poetry":
            return ["poetry", "add", *(["--group", "dev"] if args.dev else []), *args.packages]
        if pm in ("npm", "pnpm", "yarn", "bun"):
            verb = "install" if pm == "npm" else "add"
            return [pm, verb, *(["-D"] if args.dev else []), *args.packages]
        if pm == "cargo":
            return ["cargo", "add", *args.packages]
        if pm == "pip":
            venv = next((d for d in (root / ".venv", root / "venv") if d.is_dir()), None)
            if venv is None:
                raise ToolError("no project virtualenv (.venv) found; refusing to pip install globally", "NoVenv")
            py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            return [str(py), "-m", "pip", "install", *args.packages]
        raise ToolError(f"don't know how to add packages with {pm}", "NoCommand")

    def assess(self, args: InstallInput, ctx: ToolContext) -> RiskAssessment:
        root = Path(ctx.services.path_guard.check(args.path or ctx.services.extras.get("cwd") or os.getcwd(), PathOp.EXECUTE).canonical)
        try:
            a = _cmd_assess(ctx, self._argv(args, root, ctx), str(root))
        except (ToolError, ToolInputError):
            a = RiskAssessment(RiskLevel.MEDIUM)
        a.raise_to(RiskLevel.MEDIUM, "installs packages into the project environment (runs install scripts)")
        return a

    def describe(self, args: InstallInput) -> str:
        return f"install {' '.join(args.packages) or 'dependencies'} in {args.path or 'the current project'}"

    async def run(self, args: InstallInput, ctx: ToolContext) -> ToolResult:
        root = _repo(ctx, args.path)
        argv = self._argv(args, root, ctx)
        o = await _run(ctx, argv, root, 1800, label="install")
        if o.exit_code != 0:
            raise ToolError(_out(o)[-3000:], "InstallFailed")
        return self.ok(f"Installed {' '.join(args.packages) or 'dependencies'}", {"command": argv, "output": _out(o)[-4000:]})


class CodeRunInput(ToolInput):
    language: Literal["python", "node", "powershell"] = "python"
    code: str = Field(min_length=1, max_length=200_000)
    timeout_s: float = Field(60, gt=0, le=600)
    stdin_text: str | None = None


class CodeRun(Tool):
    name = "code.run"
    description = ("Run a code snippet (Python/Node/PowerShell) in a sandboxed scratch folder with a timeout. Use for "
                   "calculations, data processing and quick experiments.")
    input_model = CodeRunInput
    capabilities = ("code.exec",)
    base_risk = RiskLevel.HIGH
    side_effects = SideEffect.LOCAL
    categories = ("dev", "code")
    requires = TERMINAL
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 700.0
    timeout_field = "timeout_s"
    sensitive_args = {"code": "command"}
    resource_slot = "subprocess"

    def assess(self, args: CodeRunInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.HIGH, ["runs model-written code (not statically classifiable)"])
        if args.language == "powershell":
            cls = ctx.services.command_guard.classify(args.code, "powershell")
            a.raise_to(cls.risk, "; ".join(cls.reasons))
            if cls.denied:
                a.deny("; ".join(cls.reasons))
        low = args.code.lower()
        if re.search(r"(shutil\.rmtree|os\.remove|unlink|rmdir|subprocess|os\.system|winreg|ctypes|requests\.(post|put)|"
                     r"urllib\.request|socket\.|child_process|fs\.rm|fs\.unlink)", low):
            a.raise_to(RiskLevel.HIGH, "the code deletes files, spawns processes or uses the network")
        a.facts.command = args.code[:500]
        return a

    def describe(self, args: CodeRunInput) -> str:
        first = args.code.strip().splitlines()[0][:80] if args.code.strip() else ""
        return f"run {args.language} code ({len(args.code.splitlines())} lines): {first}"

    def approval_details(self, args: CodeRunInput) -> dict[str, Any]:
        return {"language": args.language, "code": args.code[:4000]}

    async def run(self, args: CodeRunInput, ctx: ToolContext) -> ToolResult:
        sandbox = ctx.services.settings.data_path / "sandbox" / ctx.task_id
        sandbox.mkdir(parents=True, exist_ok=True)
        ext = {"python": ".py", "node": ".js", "powershell": ".ps1"}[args.language]
        script = sandbox / f"snippet_{int(time.time() * 1000)}{ext}"
        script.write_text(args.code, encoding="utf-8")
        if args.language == "python":
            argv = [getattr(sys, "_base_executable", None) or sys.executable, "-I", str(script)]
        elif args.language == "node":
            node = shutil.which("node")
            if node is None:
                raise ToolError("Node.js is not installed", "NotFound")
            argv = [node, str(script)]
        else:
            argv = [shutil.which("powershell") or "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(script)]
        o = await run_process(argv, services=ctx.services, cancel=ctx.cancel, cwd=str(sandbox), timeout=args.timeout_s,
                              idle_timeout=args.timeout_s, task_id=ctx.task_id, output_cap=ctx.services.settings.output_cap_bytes,
                              stdin_text=args.stdin_text)
        summary = f"{args.language} exited with {o.exit_code}" if not o.timed_out else "timed out"
        res = self.ok(summary, {"exit_code": o.exit_code, "stdout": o.stdout, "stderr": o.stderr, "sandbox": str(sandbox),
                                "timed_out": o.timed_out},
                      model_view=f"{summary}\n--- stdout ---\n{o.stdout}\n--- stderr ---\n{o.stderr}", source="code.run")
        if o.timed_out:
            res.status, res.error_type = ToolStatus.TIMEOUT, "Timeout"
        return res


class RepoMapInput(ProjectInput):
    max_files: int = Field(300, ge=10, le=3000)
    include: str = Field("*", description="glob of files to include")


_SRC_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cs", ".rb", ".php", ".cpp", ".c", ".h", ".kt", ".swift"}


def _symbols(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(text) > 400_000:
        return ["(large file)"]
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return ["(syntax error)"]
        out = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)][:12]
                out.append(f"class {node.name}({', '.join(methods)})")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                out.append(f"def {node.name}")
        return out[:40]
    pats = [r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)", r"(?m)^\s*(?:export\s+)?class\s+(\w+)",
            r"(?m)^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?(\w+)",
            r"(?m)^\s*(?:pub\s+)?fn\s+(\w+)", r"(?m)^\s*(?:public|private|protected|internal)[\w\s<>]*\s(\w+)\s*\("]
    names: list[str] = []
    for p in pats:
        names += re.findall(p, text)
    return list(dict.fromkeys(names))[:40]


class CodeRepoMap(Tool):
    name = "code.repo_map"
    description = "Map a codebase: source files with their top-level classes/functions (bounded). Good first step for code tasks."
    input_model = RepoMapInput
    capabilities = ("fs.read",)
    categories = ("dev", "code")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 120.0

    async def run(self, args: RepoMapInput, ctx: ToolContext) -> ToolResult:
        root = _repo(ctx, args.path)
        import asyncio
        import fnmatch

        def build() -> list[tuple[str, list[str]]]:
            out: list[tuple[str, list[str]]] = []
            skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".mypy_cache"}
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
                for f in sorted(files):
                    p = Path(dirpath) / f
                    if p.suffix.lower() in _SRC_EXTS and fnmatch.fnmatch(f, args.include):
                        out.append((str(p.relative_to(root)), _symbols(p)))
                        if len(out) >= args.max_files:
                            return out
            return out

        entries = await asyncio.to_thread(build)
        view = "\n".join(f"{rel}: {', '.join(syms)}" if syms else rel for rel, syms in entries)
        info = detect_project(root, _preferred_pm(ctx, root))
        header = f"{root} — {', '.join(info.kinds) or 'unknown'} project; tests: {' '.join(info.test_command or []) or '?'}"
        return self.ok(f"Mapped {len(entries)} source files", {"root": str(root), "files": [e[0] for e in entries],
                                                                  "project": info.as_dict()},
                       model_view=header + "\n" + view, source=f"repo:{root}")


TOOLS: list[type[Tool]] = [GitStatus, GitDiff, GitLog, GitBranch, GitCommit, GitStash, GitIntegrate, GitPush, GitClone,
                           DevDetect, DevRunTests, DevTooling, DevInstall, CodeRun, CodeRepoMap]
