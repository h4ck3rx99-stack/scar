"""App launcher and controller (C9.4): search the index, launch with args, open folders in editors,
open files/URLs with their default handler, and verify a matching window appears."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psutil
from pydantic import Field

from scar.core.errors import ToolError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.apps.index import AppEntry, vscode_cli
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path
from scar.tools.windows import win32

WIN = Requires(platform="win32", setting="pc_control_enabled", setup_doc="docs/troubleshooting.md#windows-automation")
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
EDITOR_NAMES = {"code", "vs code", "vscode", "visual studio code", "cursor", "zed", "antigravity"}
EXEC_EXTS = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".scr", ".com", ".hta", ".msix", ".appx", ".jar"}


def _aliases(ctx: ToolContext) -> dict[str, str]:
    mem = ctx.services.memory
    if mem is None:
        return {}
    try:
        return dict(mem.aliases("app"))
    except (AttributeError, OSError):
        return {}


class ListInput(ToolInput):
    query: str = Field(description="app name to look up, e.g. 'vs code', 'spotify'")
    limit: int = Field(8, ge=1, le=30)


class AppsFind(Tool):
    name = "apps.find"
    description = "Look up installed apps (Start Menu, App Paths, packaged apps, PATH) by name."
    input_model = ListInput
    capabilities = ("apps.read",)
    categories = ("apps",)
    requires = Requires(platform="win32")

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        idx = ctx.services.app_index
        hits = await asyncio.to_thread(idx.search, args.query, args.limit, _aliases(ctx))
        view = "\n".join(f"{e.name} [{e.kind}] -> {e.target} (score {s:.2f})" for e, s in hits)
        return self.ok(f"{len(hits)} app(s) match '{args.query}'", {"apps": [dict(e.as_dict(), score=s) for e, s in hits]},
                       model_view=view or "no match")


class LaunchInput(ToolInput):
    app: str = Field(description="app name ('vs code', 'chrome') or full path to an .exe/.lnk")
    args: list[str] = Field(default_factory=list, description="command-line arguments")
    folder: str | None = Field(None, description="folder/workspace to open (editors like VS Code)")
    wait_for_window: bool = True
    expect_title: str | None = Field(None, description="substring the new window title must contain")
    timeout_s: float = Field(40.0, gt=1, le=180)


class AppsLaunch(Tool):
    name = "apps.launch"
    description = ("Launch an application (optionally with arguments or a folder, e.g. VS Code on a project) and "
                   "verify its window appears. Apps are started detached so they keep running after SCAR exits.")
    input_model = LaunchInput
    capabilities = ("apps.launch",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("apps", "windows", "dev")
    requires = WIN
    timeout = 200.0
    timeout_field = "timeout_s"
    sensitive_args = {"app": "command", "args": "command", "folder": "path"}

    def assess(self, args: LaunchInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        a.facts.app = args.app.lower().removesuffix(".exe")
        if args.folder:
            chk = ctx.services.path_guard.check(args.folder, PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
            a.facts.paths.append(chk.canonical)
        if os.path.isabs(args.app):
            chk = ctx.services.path_guard.check(args.app, PathOp.EXECUTE)
            a.raise_to(chk.risk if chk.risk > RiskLevel.MEDIUM else RiskLevel.MEDIUM, "; ".join(chk.reasons))
            if chk.denied:
                a.deny("; ".join(chk.reasons))
            if _downloaded(Path(chk.canonical)):
                a.raise_to(RiskLevel.CRITICAL, "the program was downloaded from the internet")
        if args.args:
            cls = ctx.services.command_guard.classify_argv([args.app, *args.args])
            if cls.risk > a.level:
                a.raise_to(cls.risk, "; ".join(cls.reasons))
            if cls.denied:
                a.deny("; ".join(cls.reasons))
        return a

    def describe(self, args: LaunchInput) -> str:
        return f"open {args.app}" + (f" in {args.folder}" if args.folder else "") + (f" {' '.join(args.args)}" if args.args else "")

    def progress_line(self, args: LaunchInput) -> str | None:
        return f"Opening {args.app}."

    async def _resolve(self, args: LaunchInput, ctx: ToolContext) -> AppEntry:
        if os.path.isabs(args.app) or (args.app.lower().endswith((".exe", ".lnk")) and os.path.exists(args.app)):
            p = check_path(ctx, args.app, PathOp.EXECUTE).path
            if not p.exists():
                raise ToolError(f"not found: {p}", "NotFound")
            return AppEntry(name=p.stem, kind="path", target=str(p), exe_name=p.name.lower())
        if args.app.lower() in EDITOR_NAMES and args.app.lower() not in ("cursor", "zed", "antigravity"):
            code = vscode_cli()
            if code:
                return AppEntry(name="Visual Studio Code", kind="path", target=code, exe_name="code.exe")
        hits = await asyncio.to_thread(ctx.services.app_index.search, args.app, 5, _aliases(ctx))
        if not hits or hits[0][1] < 0.6:
            raise ToolError(f"no installed app matches '{args.app}'", "NotFound")
        top = [h for h in hits if h[1] >= hits[0][1] - 0.02]
        exes = {Path(h[0].target).name.lower() for h in top if h[0].target.lower().endswith(".exe")}
        if len(exes) > 1:
            names = ", ".join(f"{h[0].name} ({Path(h[0].target).name})" for h in top[:4])
            raise ToolError(f"'{args.app}' is ambiguous: {names}", "Ambiguous")
        exe_hit = next((h for h in top if h[0].target.lower().endswith(".exe")), None)
        if exe_hit is not None:
            return exe_hit[0]
        return hits[0][0]

    async def run(self, args: LaunchInput, ctx: ToolContext) -> ToolResult:
        entry = await self._resolve(args, ctx)
        argv_extra = list(args.args)
        folder = None
        if args.folder:
            fchk = check_path(ctx, args.folder, PathOp.READ)
            folder = fchk.path
            if not folder.exists():
                raise ToolError(f"folder not found: {folder}", "NotFound")
            argv_extra = [str(folder), *argv_extra]
        before = {w.hwnd for w in await asyncio.to_thread(win32.list_windows)}
        started = time.time()
        pid = await asyncio.to_thread(_launch, entry, argv_extra)
        exe_name = _expected_process(entry)
        expect = args.expect_title or (folder.name if folder else None)
        data: dict[str, Any] = {"app": entry.name, "target": entry.target, "kind": entry.kind, "pid": pid,
                                "expected_process": exe_name, "expect_title": expect, "folder": str(folder) if folder else None,
                                "started": started}
        if not args.wait_for_window:
            return self.ok(f"Launched {entry.name}", data)
        w = await asyncio.to_thread(_wait_for_app_window, exe_name, expect, before, args.timeout_s, pid)
        if w is None:
            data["window"] = None
            return self.ok(f"Launched {entry.name}, but no matching window appeared within {args.timeout_s:.0f}s", data)
        data["window"] = w.as_dict()
        await asyncio.to_thread(win32.focus_window, w.hwnd)
        label = f"Opened {folder.name} in {entry.name}" if folder else f"Opened {entry.name}"
        return self.ok(label, data)

    async def verify(self, args: LaunchInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        if not args.wait_for_window:
            pid = result.data.get("pid")
            return VerificationResult.from_checks([Check(name="process started", passed=bool(pid) and psutil.pid_exists(pid))]) \
                if pid else VerificationResult.unverifiable("launched through the shell; no PID to check")
        w = result.data.get("window")
        exe = result.data.get("expected_process")
        expect = result.data.get("expect_title")
        checks = [Check(name="window appeared", passed=w is not None)]
        if w is not None:
            alive = win32.window_exists(w["hwnd"])
            checks.append(Check(name="window still open", passed=alive))
            if exe:
                checks.append(Check(name=f"process is {exe}", passed=w["process"].lower() == exe.lower(), detail=w["process"]))
            if expect:
                title = win32.window_info(w["hwnd"]).title if alive else w["title"]
                checks.append(Check(name=f"title contains '{expect}'", passed=expect.lower() in title.lower(), detail=title[:80]))
        return VerificationResult.from_checks(checks, {"window": w})


def _norm_name(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _expected_process(entry: AppEntry) -> str | None:
    t = entry.target.lower()
    if t.endswith(("code.cmd", "\\code", "/code")) or entry.exe_name in ("code.cmd", "code.exe"):
        return "Code.exe"
    if t.endswith(".exe"):
        return Path(entry.target).name
    return None


def _launch(entry: AppEntry, extra: list[str]) -> int | None:
    """Start detached (outlives SCAR). Returns a PID when the launched process is the app itself."""
    import subprocess as sp

    if entry.kind == "packaged":
        sp.Popen(["explorer.exe", "shell:AppsFolder\\" + entry.target], creationflags=DETACHED)
        return None
    target = entry.target
    if target.lower().endswith(".lnk"):
        # unresolvable shortcut: let the shell run it (ShellExecute passes parameters through)
        import win32api

        win32api.ShellExecute(0, "open", target, sp.list2cmdline(extra), None, 1)
        return None
    shortcut_args = entry.args.split() if entry.args and not extra else []
    argv = [target, *shortcut_args, *extra]
    if target.lower().endswith((".cmd", ".bat")):
        # e.g. VS Code's code.cmd launcher: run via cmd without a console window; the editor process is separate
        sp.Popen([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *argv], creationflags=DETACHED | 0x08000000,
                 stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        return None
    proc = sp.Popen(argv, creationflags=DETACHED, stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL, close_fds=True)
    return proc.pid


def _wait_for_app_window(exe: str | None, expect_title: str | None, before: set[int], timeout: float,
                         pid: int | None) -> win32.WindowInfo | None:
    deadline = time.monotonic() + timeout
    fallback: win32.WindowInfo | None = None
    while time.monotonic() < deadline:
        for w in win32.list_windows():
            if exe and w.process.lower() != exe.lower() and (pid is None or w.pid != pid):
                continue
            if expect_title and expect_title.lower() not in w.title.lower():
                continue
            if not exe and not expect_title and w.hwnd in before and (pid is None or w.pid != pid):
                continue
            if w.hwnd not in before or expect_title:
                return w
            fallback = fallback or w
        time.sleep(0.4)
    return fallback


def _downloaded(p: Path) -> bool:
    try:
        return os.path.exists(str(p) + ":Zone.Identifier")
    except OSError:
        return False


class OpenInput(ToolInput):
    target: str = Field(description="file, folder or URL to open with its default app")


class AppsOpen(Tool):
    name = "apps.open"
    description = "Open a file, folder or URL with its default application (e.g. a folder in File Explorer, a PDF, a URL)."
    input_model = OpenInput
    capabilities = ("apps.open",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("apps", "fs", "web")
    requires = WIN
    sensitive_args = {"target": "url"}

    def assess(self, args: OpenInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        parsed = urlparse(args.target)
        if parsed.scheme in ("http", "https"):
            a.facts.domains.append((parsed.hostname or "").lower())
            return a
        if parsed.scheme and len(parsed.scheme) > 1 and parsed.scheme not in ("file",):
            a.raise_to(RiskLevel.HIGH, f"opens a {parsed.scheme}: link (hands data to another app)")
            return a
        chk = ctx.services.path_guard.check(args.target, PathOp.EXECUTE)
        if chk.denied:
            a.deny("; ".join(chk.reasons), "secret_paths")
        a.facts.paths.append(chk.canonical)
        p = Path(chk.canonical)
        if p.suffix.lower() in EXEC_EXTS:
            a.raise_to(RiskLevel.HIGH, "runs an executable or script")
            if _downloaded(p):
                a.raise_to(RiskLevel.CRITICAL, "the file was downloaded from the internet; downloads are never auto-executed")
        return a

    def describe(self, args: OpenInput) -> str:
        return f"open {args.target}"

    async def run(self, args: OpenInput, ctx: ToolContext) -> ToolResult:
        parsed = urlparse(args.target)
        if parsed.scheme in ("http", "https"):
            await asyncio.to_thread(os.startfile, args.target)  # type: ignore[attr-defined]
            return self.ok(f"Opened {args.target} in the default browser", {"target": args.target, "url": True})
        p = check_path(ctx, args.target, PathOp.EXECUTE).path
        if not p.exists():
            raise ToolError(f"not found: {p}", "NotFound")
        await asyncio.to_thread(os.startfile, str(p))  # type: ignore[attr-defined]
        return self.ok(f"Opened {p.name}", {"target": str(p), "url": False, "is_dir": p.is_dir()})

    async def verify(self, args: OpenInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        if result.data.get("is_dir"):
            name = Path(result.data["target"]).name
            w = await asyncio.to_thread(win32.wait_for_window, lambda w: w.process.lower() == "explorer.exe" and
                                        name.lower() in w.title.lower(), 10.0)
            return VerificationResult.from_checks([Check(name="Explorer window open", passed=w is not None)])
        return VerificationResult.unverifiable("handed to the default application")


TOOLS: list[type[Tool]] = [AppsFind, AppsLaunch, AppsOpen]
