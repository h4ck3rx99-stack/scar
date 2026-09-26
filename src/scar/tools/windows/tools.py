"""Window manager and UI Automation tools (C9.4)."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

import psutil
from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.windows import win32

WIN = Requires(platform="win32", setting="pc_control_enabled", setup_doc="docs/troubleshooting.md#windows-automation")


class WindowTarget(ToolInput):
    hwnd: int | None = Field(None, description="window handle from windows.list")
    title: str | None = Field(None, description="substring of the window title")
    process: str | None = Field(None, description="process name, e.g. code.exe")


def resolve_window(t: WindowTarget) -> win32.WindowInfo:
    if t.hwnd:
        if not win32.window_exists(t.hwnd):
            raise ToolError(f"window {t.hwnd} no longer exists", "NotFound")
        return win32.window_info(t.hwnd)
    if not t.title and not t.process:
        raise ToolInputError("give hwnd, title or process")
    matches = win32.find_windows(title=t.title, process=t.process)
    if not matches:
        raise ToolError(f"no window matching title={t.title!r} process={t.process!r}", "NotFound")
    if len(matches) > 1:
        exact = [m for m in matches if t.title and m.title.lower() == t.title.lower()]
        if len(exact) == 1:
            return exact[0]
        fg = [m for m in matches if m.foreground]
        if len(fg) == 1:
            return fg[0]
        names = "; ".join(f"{m.hwnd}: {m.title[:50]}" for m in matches[:6])
        raise ToolError(f"{len(matches)} windows match — pick one by hwnd: {names}", "Ambiguous")
    return matches[0]


def _uipi_note(w: win32.WindowInfo) -> str | None:
    if win32.is_process_elevated(w.pid) and not win32.self_elevated():
        return (f"'{w.title}' runs as administrator; Windows (UIPI) blocks a non-elevated SCAR from controlling it. "
                "SCAR never self-elevates.")
    return None


class ListInput(ToolInput):
    title: str | None = None
    process: str | None = None


class WindowsList(Tool):
    name = "windows.list"
    description = "List top-level app windows: handle, title, process, position/size, state, which one is focused."
    input_model = ListInput
    capabilities = ("windows.read",)
    categories = ("windows", "apps", "screen")
    requires = Requires(platform="win32")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL  # window titles are external content (web page titles etc.)

    async def run(self, args: ListInput, ctx: ToolContext) -> ToolResult:
        ws = await asyncio.to_thread(win32.find_windows, args.title, args.process)
        lines = [f"{w.hwnd}: [{w.process}] {w.title[:80]} ({w.left},{w.top} {w.width}x{w.height})"
                 f"{' *focused*' if w.foreground else ''}{' minimized' if w.minimized else ''}{' NOT RESPONDING' if w.hung else ''}"
                 for w in ws]
        return self.ok(f"{len(ws)} windows", {"windows": [w.as_dict() for w in ws], "monitors":
                                              [m.__dict__ for m in win32.list_monitors()]}, model_view="\n".join(lines))


class FocusInput(WindowTarget):
    pass


class WindowsFocus(Tool):
    name = "windows.focus"
    description = "Bring a window to the front and focus it (restores it if minimized)."
    input_model = FocusInput
    capabilities = ("windows.control",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("windows", "apps")
    requires = WIN

    def assess(self, args: FocusInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        a.facts.app = (args.process or "").lower().removesuffix(".exe") or None
        return a

    def describe(self, args: FocusInput) -> str:
        return f"focus window {args.title or args.process or args.hwnd}"

    async def run(self, args: FocusInput, ctx: ToolContext) -> ToolResult:
        w = await asyncio.to_thread(resolve_window, args)
        note = _uipi_note(w)
        ok = await asyncio.to_thread(win32.focus_window, w.hwnd)
        if not ok:
            raise ToolError(note or f"Windows refused to bring '{w.title}' to the front", "FocusRefused")
        return self.ok(f"Focused {w.title[:60]}", {"hwnd": w.hwnd, "title": w.title, "process": w.process})

    async def verify(self, args: FocusInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        fg = win32.foreground_hwnd()
        return VerificationResult.from_checks([Check(name="window is foreground", passed=fg == result.data["hwnd"])])


class ArrangeInput(WindowTarget):
    action: Literal["move", "resize", "move_resize", "minimize", "maximize", "restore", "snap_left", "snap_right"]
    left: int | None = None
    top: int | None = None
    width: int | None = Field(None, gt=50)
    height: int | None = Field(None, gt=50)
    monitor: int | None = Field(None, description="monitor index for snap/move (from windows.list monitors)")


class WindowsArrange(Tool):
    name = "windows.arrange"
    description = "Move, resize, minimize, maximize, restore or snap a window (virtual-screen pixels)."
    input_model = ArrangeInput
    capabilities = ("windows.control",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("windows",)
    requires = WIN

    def describe(self, args: ArrangeInput) -> str:
        return f"{args.action} window {args.title or args.process or args.hwnd}"

    async def run(self, args: ArrangeInput, ctx: ToolContext) -> ToolResult:
        w = await asyncio.to_thread(resolve_window, args)
        note = _uipi_note(w)
        if note:
            raise ToolError(note, "ElevatedTarget")
        if args.action in ("minimize", "maximize", "restore"):
            await asyncio.to_thread(win32.set_state, w.hwnd, args.action)
        elif args.action in ("snap_left", "snap_right"):
            mons = win32.list_monitors()
            mon = mons[args.monitor] if args.monitor is not None and args.monitor < len(mons) else next(
                (m for m in mons if m.left <= (w.left + w.right) // 2 < m.right), mons[0])
            half = (mon.right - mon.left) // 2
            x = mon.left if args.action == "snap_left" else mon.left + half
            await asyncio.to_thread(win32.move_resize, w.hwnd, x, mon.top, half, mon.bottom - mon.top)
        else:
            if args.monitor is not None and args.left is None:
                mons = win32.list_monitors()
                if args.monitor >= len(mons):
                    raise ToolInputError(f"monitor {args.monitor} does not exist")
                args.left, args.top = mons[args.monitor].left + 50, mons[args.monitor].top + 50
            await asyncio.to_thread(win32.move_resize, w.hwnd, args.left, args.top, args.width, args.height)
        await asyncio.sleep(0.25)
        after = win32.window_info(w.hwnd)
        return self.ok(f"Window {args.action.replace('_', ' ')} done", {"hwnd": w.hwnd, "after": after.as_dict()})

    async def verify(self, args: ArrangeInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        w = win32.window_info(result.data["hwnd"])
        checks: list[Check] = []
        if args.action == "minimize":
            checks.append(Check(name="minimized", passed=w.minimized))
        elif args.action == "maximize":
            checks.append(Check(name="maximized", passed=w.maximized))
        elif args.action == "restore":
            checks.append(Check(name="not minimized", passed=not w.minimized))
        else:
            if args.left is not None:
                checks.append(Check(name="left", passed=abs(w.left - args.left) <= 8, detail=str(w.left)))
            if args.top is not None:
                checks.append(Check(name="top", passed=abs(w.top - args.top) <= 8, detail=str(w.top)))
            if args.width is not None:
                checks.append(Check(name="width", passed=abs(w.width - args.width) <= 16, detail=str(w.width)))
            if args.height is not None:
                checks.append(Check(name="height", passed=abs(w.height - args.height) <= 16, detail=str(w.height)))
        return VerificationResult.from_checks(checks, {"rect": [w.left, w.top, w.width, w.height]})


class CloseInput(WindowTarget):
    force: bool = Field(False, description="kill the owning process if it does not close (HIGH risk)")
    wait_s: float = Field(5.0, ge=0.5, le=60)


class WindowsClose(Tool):
    name = "windows.close"
    description = "Close a window gracefully (like clicking X). force=true kills the app if it will not close."
    input_model = CloseInput
    capabilities = ("windows.control",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("windows", "apps")
    requires = WIN

    def assess(self, args: CloseInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM, ["closes a window (the app may prompt to save)"])
        if args.force:
            a.raise_to(RiskLevel.HIGH, "may force-kill the application (unsaved work is lost)")
        a.facts.app = (args.process or "").lower().removesuffix(".exe") or None
        return a

    def describe(self, args: CloseInput) -> str:
        return f"{'force-' if args.force else ''}close window {args.title or args.process or args.hwnd}"

    async def run(self, args: CloseInput, ctx: ToolContext) -> ToolResult:
        w = await asyncio.to_thread(resolve_window, args)
        if args.force and (w.pid in (0, 4) or w.process.lower() in ("explorer.exe", "csrss.exe", "dwm.exe", "winlogon.exe")):
            raise ToolError("refusing to force-close a system process window", "Refused")
        win32.close_window(w.hwnd)
        deadline = time.monotonic() + args.wait_s
        while time.monotonic() < deadline and win32.window_exists(w.hwnd):
            await asyncio.sleep(0.25)
        forced = False
        if win32.window_exists(w.hwnd):
            if not args.force:
                return self.ok(f"Asked '{w.title[:50]}' to close; it is still open (maybe a save prompt)",
                               {"hwnd": w.hwnd, "closed": False, "pid": w.pid})
            try:
                psutil.Process(w.pid).kill()
                forced = True
            except psutil.Error as exc:
                raise ToolError(f"could not kill PID {w.pid}: {exc}", "KillFailed") from exc
            await asyncio.sleep(0.5)
        return self.ok(f"Closed {w.title[:60]}" + (" (forced)" if forced else ""),
                       {"hwnd": w.hwnd, "closed": True, "forced": forced, "pid": w.pid})

    async def verify(self, args: CloseInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        if not result.data.get("closed"):
            return VerificationResult(verified=None, note="window still open (awaiting the app)")
        return VerificationResult.from_checks([Check(name="window gone", passed=not win32.window_exists(result.data["hwnd"]))])


class WaitWindowInput(ToolInput):
    title: str | None = None
    process: str | None = None
    timeout_s: float = Field(20.0, gt=0, le=300)


class WindowsWait(Tool):
    name = "windows.wait"
    description = "Wait (bounded) until a window with the given title substring and/or process appears."
    input_model = WaitWindowInput
    capabilities = ("windows.read",)
    categories = ("windows", "apps")
    requires = Requires(platform="win32")
    timeout = 320.0
    timeout_field = "timeout_s"

    async def run(self, args: WaitWindowInput, ctx: ToolContext) -> ToolResult:
        if not args.title and not args.process:
            raise ToolInputError("give title and/or process")

        def pred(w: win32.WindowInfo) -> bool:
            if args.title and args.title.lower() not in w.title.lower():
                return False
            return not (args.process and args.process.lower().removesuffix(".exe") != w.process.lower().removesuffix(".exe"))

        w = await asyncio.to_thread(win32.wait_for_window, pred, args.timeout_s)
        if w is None:
            raise ToolError(f"no matching window appeared within {args.timeout_s:.0f}s", "Timeout")
        return self.ok(f"Window ready: {w.title[:60]}", {"window": w.as_dict()})


# ---------------------------------------------------------------- UI Automation
class InspectInput(WindowTarget):
    max_depth: int = Field(6, ge=1, le=30)
    max_elements: int = Field(200, ge=10, le=2000)


class UiaInspect(Tool):
    name = "uia.inspect"
    description = ("Show a window's UI Automation tree (buttons, fields, menus with [id], names, values). Use the ids "
                   "with uia.act. Defaults to the foreground window.")
    input_model = InspectInput
    capabilities = ("uia.read",)
    categories = ("windows", "apps", "screen")
    requires = Requires(platform="win32", setup_doc="docs/troubleshooting.md#windows-automation")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "screen"
    timeout = 60.0

    async def run(self, args: InspectInput, ctx: ToolContext) -> ToolResult:
        hwnd = None
        if args.hwnd or args.title or args.process:
            w = await asyncio.to_thread(resolve_window, args)
            hwnd = w.hwnd
            note = _uipi_note(w)
            if note:
                raise ToolError(note, "ElevatedTarget")
        else:
            hwnd = win32.foreground_hwnd()
        uia = ctx.services.uia
        elems = await uia.call(lambda: uia.inspect_sync(hwnd, args.max_depth, args.max_elements))
        view = "\n".join(e.line() for e in elems)
        return self.ok(f"{len(elems)} UI elements", {"hwnd": hwnd, "elements": [e.as_dict() for e in elems]},
                       model_view=view, source=f"uia:{hwnd}")


class FindInput(WindowTarget):
    name_contains: str | None = None
    control_type: str | None = Field(None, description="Button, Edit, MenuItem, ListItem, CheckBox, Hyperlink, …")
    automation_id: str | None = None


class UiaFind(Tool):
    name = "uia.find"
    description = "Find UI elements in a window by name, control type and/or automation id."
    input_model = FindInput
    capabilities = ("uia.read",)
    categories = ("windows", "apps")
    requires = Requires(platform="win32")
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "screen"
    timeout = 60.0

    async def run(self, args: FindInput, ctx: ToolContext) -> ToolResult:
        hwnd = (await asyncio.to_thread(resolve_window, args)).hwnd if (args.hwnd or args.title or args.process) else win32.foreground_hwnd()
        uia = ctx.services.uia
        found = await uia.call(lambda: uia.find_sync(hwnd, args.name_contains, args.control_type, args.automation_id))
        return self.ok(f"{len(found)} matching elements", {"hwnd": hwnd, "elements": [e.as_dict() for e in found]},
                       model_view="\n".join(e.line().strip() + f" at {e.center}" for e in found), source=f"uia:{hwnd}")


class ActInput(ToolInput):
    element_id: int = Field(description="[id] from uia.inspect / uia.find")
    action: Literal["invoke", "set_value", "toggle", "select", "expand", "collapse", "focus", "scroll_into_view", "get_text"]
    value: str | None = None


class UiaAct(Tool):
    name = "uia.act"
    description = ("Act on a UI element via UI Automation patterns (invoke a button, set a field's value, toggle, "
                   "select, expand, read text). Preferred over mouse clicks.")
    input_model = ActInput
    capabilities = ("uia.control",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("windows", "apps")
    requires = WIN
    sensitive_args = {"value": "body"}

    def assess(self, args: ActInput, ctx: ToolContext) -> RiskAssessment:
        if args.action == "get_text":
            return RiskAssessment(RiskLevel.LOW)
        a = RiskAssessment(RiskLevel.MEDIUM)
        try:
            ctrl = ctx.services.uia.element(args.element_id)
            label = f"{ctrl.Name or ''}".lower()
        except ToolError:
            label = ""
        if any(w in label for w in ("send", "delete", "remove", "pay", "buy", "purchase", "submit", "confirm", "publish",
                                    "post", "transfer", "uninstall", "format")):
            a.raise_to(RiskLevel.HIGH, f"the control '{label[:40]}' may perform an irreversible action")
        if any(w in label for w in ("pay", "buy", "purchase", "place order", "transfer")):
            a.raise_to(RiskLevel.CRITICAL, "looks like a financial action")
        return a

    def describe(self, args: ActInput) -> str:
        return f"{args.action} UI element [{args.element_id}]" + (f" with '{args.value[:60]}'" if args.value else "")

    async def run(self, args: ActInput, ctx: ToolContext) -> ToolResult:
        uia = ctx.services.uia
        text = await uia.call(lambda: uia.act_sync(args.element_id, args.action, args.value))
        if args.action == "get_text":
            res = self.ok(f"Read {len(text)} characters", {"text": text}, model_view=text)
            res.provenance = res.provenance.external(f"uia-text:{args.element_id}")
            return res
        return self.ok(f"UI action {args.action} done", {"element_id": args.element_id, "action": args.action})

    async def verify(self, args: ActInput, result: ToolResult, ctx: ToolContext) -> VerificationResult | None:
        if args.action == "get_text":
            return None
        uia = ctx.services.uia
        try:
            value, state = await uia.call(lambda: uia.value_of_sync(args.element_id))
        except (ToolError, OSError):
            return VerificationResult.unverifiable("element no longer available after the action")
        if args.action == "set_value":
            return VerificationResult.from_checks([Check(name="value set", passed=(value or "") == (args.value or ""),
                                                         detail=(value or "")[:60])])
        if args.action == "toggle" and state is not None:
            return VerificationResult(verified=None, note=f"toggle state now {'on' if state else 'off'}",
                                      evidence={"state": state})
        return VerificationResult.unverifiable("no deterministic postcondition for this pattern")


TOOLS: list[type[Tool]] = [WindowsList, WindowsFocus, WindowsArrange, WindowsClose, WindowsWait, UiaInspect, UiaFind, UiaAct]

