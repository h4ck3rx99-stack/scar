"""Keyboard, mouse and clipboard tools (C9.5).

Typing and clicking require a target window; SCAR focuses it, verifies it is
in the foreground, and re-checks focus between every chunk of input. If focus
moves, input stops immediately (FocusLost).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.redaction import global_redactor
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path
from scar.tools.input import sendinput as si
from scar.tools.windows import win32
from scar.tools.windows.tools import WindowTarget, resolve_window

WIN = Requires(platform="win32", setting="pc_control_enabled", setup_doc="docs/troubleshooting.md#windows-automation")

_DANGEROUS_COMBOS = {"win+r": "opens the Run dialog", "win+x": "opens the power-user menu", "ctrl+alt+delete": "secure attention",
                     "alt+f4": "closes the focused window", "ctrl+shift+esc": "opens Task Manager", "win+l": "locks the PC"}


async def _prepare_target(args: WindowTarget) -> tuple[win32.WindowInfo, object]:
    w = await asyncio.to_thread(resolve_window, args)
    if win32.is_process_elevated(w.pid) and not win32.self_elevated():
        raise ToolError(f"'{w.title}' runs elevated; Windows blocks input from non-elevated apps (UIPI). SCAR never "
                        "self-elevates.", "ElevatedTarget")
    if not await asyncio.to_thread(win32.focus_window, w.hwnd):
        raise ToolError(f"could not focus '{w.title}' — input not sent", "FocusRefused")
    await asyncio.sleep(0.15)
    guard = si.focus_guard(w.hwnd)
    guard()
    return w, guard


class TypeInput(WindowTarget):
    text: str = Field(min_length=1, max_length=20000)
    press_enter: bool = False


class InputType(Tool):
    name = "input.type"
    description = ("Type text into a specific window (by hwnd/title/process). Prefer uia.act set_value or browser.type "
                   "when possible. Aborts if focus changes.")
    input_model = TypeInput
    capabilities = ("input.keyboard",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("input", "windows", "apps")
    requires = WIN
    timeout = 300.0
    sensitive_args = {"text": "body"}

    def assess(self, args: TypeInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        kinds = global_redactor().contains_secret(args.text)
        if kinds:
            a.raise_to(RiskLevel.CRITICAL, "typing text that looks like a password or secret")
        if args.press_enter:
            a.raise_to(RiskLevel.HIGH, "presses Enter afterwards (may submit or send)")
        a.facts.app = (args.process or "").lower().removesuffix(".exe") or None
        return a

    def describe(self, args: TypeInput) -> str:
        preview = args.text if len(args.text) <= 80 else args.text[:77] + "…"
        return f"type '{preview}' into {args.title or args.process or args.hwnd}" + (" and press Enter" if args.press_enter else "")

    async def run(self, args: TypeInput, ctx: ToolContext) -> ToolResult:
        w, guard = await _prepare_target(args)
        typed = await asyncio.to_thread(si.type_text, args.text, guard)  # type: ignore[arg-type]
        if args.press_enter:
            await asyncio.to_thread(si.hotkey, "enter", guard)  # type: ignore[arg-type]
        return self.ok(f"Typed {typed} characters into {w.title[:50]}", {"hwnd": w.hwnd, "chars": typed})

    async def verify(self, args: TypeInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        # verify via UIA: the focused element's value (or window text) contains what we typed
        uia = ctx.services.uia
        if uia is None:
            return VerificationResult.unverifiable("UI Automation unavailable")
        hwnd = result.data["hwnd"]
        try:
            text = await uia.call(lambda: uia.window_text_sync(hwnd, 40000))
        except (ToolError, OSError):
            return VerificationResult.unverifiable("could not read the window back")
        probe = args.text.strip().splitlines()[-1][-40:] if args.text.strip() else ""
        if not probe:
            return VerificationResult.unverifiable("nothing to verify")
        if probe in text:
            return VerificationResult.from_checks([Check(name="typed text visible in window", passed=True)])
        return VerificationResult.unverifiable("typed text not exposed through UI Automation (common for custom editors)")


class HotkeyInput(WindowTarget):
    keys: str = Field(description="e.g. 'ctrl+s', 'alt+tab', 'enter', 'ctrl+shift+p'")
    repeat: int = Field(1, ge=1, le=50)


class InputHotkey(Tool):
    name = "input.hotkey"
    description = "Press a key or key combination in a specific window (e.g. ctrl+s, enter, f5)."
    input_model = HotkeyInput
    capabilities = ("input.keyboard",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("input", "windows", "apps")
    requires = WIN

    def assess(self, args: HotkeyInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        combo = args.keys.lower().replace(" ", "")
        if combo in _DANGEROUS_COMBOS:
            a.raise_to(RiskLevel.HIGH, _DANGEROUS_COMBOS[combo])
        if combo in ("enter", "return", "ctrl+enter"):
            a.raise_to(RiskLevel.MEDIUM, "Enter may submit a form")
        try:
            si.parse_hotkey(args.keys)
        except ToolInputError as exc:
            a.reasons.append(str(exc))
        return a

    def describe(self, args: HotkeyInput) -> str:
        return f"press {args.keys}" + (f" ×{args.repeat}" if args.repeat > 1 else "") + f" in {args.title or args.process or args.hwnd}"

    async def run(self, args: HotkeyInput, ctx: ToolContext) -> ToolResult:
        si.parse_hotkey(args.keys)
        w, guard = await _prepare_target(args)
        for _ in range(args.repeat):
            await asyncio.to_thread(si.hotkey, args.keys, guard)  # type: ignore[arg-type]
        return self.ok(f"Pressed {args.keys}", {"hwnd": w.hwnd, "keys": args.keys})


class MouseInput(ToolInput):
    action: Literal["click", "double_click", "right_click", "move", "drag", "scroll"]
    x: int
    y: int
    x2: int | None = None
    y2: int | None = None
    scroll_clicks: int = Field(-3, description="negative scrolls down")
    expect_hwnd: int | None = Field(None, description="window that must be under/owning the point (focus safety)")
    reason: str = Field("", description="what is being clicked, e.g. 'the blue Save button'")


class InputMouse(Tool):
    name = "input.mouse"
    description = ("Mouse click/double/right/move/drag/scroll at virtual-screen pixel coordinates. Prefer uia.act or "
                   "vision.click_target (grounded) over raw coordinates.")
    input_model = MouseInput
    capabilities = ("input.mouse",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("input", "screen")
    requires = WIN

    def assess(self, args: MouseInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW if args.action in ("move", "scroll") else RiskLevel.MEDIUM)
        label = args.reason.lower()
        if any(w in label for w in ("send", "delete", "submit", "publish", "confirm", "post")):
            a.raise_to(RiskLevel.HIGH, f"clicking '{args.reason[:40]}' may be irreversible")
        if any(w in label for w in ("pay", "buy", "purchase", "place order", "checkout")):
            a.raise_to(RiskLevel.CRITICAL, "looks like a financial action")
        return a

    def describe(self, args: MouseInput) -> str:
        return f"{args.action.replace('_', ' ')} at ({args.x}, {args.y})" + (f" — {args.reason}" if args.reason else "")

    async def run(self, args: MouseInput, ctx: ToolContext) -> ToolResult:
        vl, vt, vw, vh = win32.virtual_screen()
        for x, y in [(args.x, args.y)] + ([(args.x2, args.y2)] if args.x2 is not None and args.y2 is not None else []):
            if not (vl <= x < vl + vw and vt <= y < vt + vh):
                raise ToolInputError(f"({x}, {y}) is outside the virtual screen ({vl},{vt} {vw}x{vh})")
        guard = None
        if args.expect_hwnd:
            if not await asyncio.to_thread(win32.focus_window, args.expect_hwnd):
                raise ToolError("could not focus the expected window", "FocusRefused")
            guard = si.focus_guard(args.expect_hwnd)
            guard()
        if args.action == "move":
            await asyncio.to_thread(si.move, args.x, args.y)
        elif args.action in ("click", "double_click", "right_click"):
            button = "right" if args.action == "right_click" else "left"
            await asyncio.to_thread(si.click, args.x, args.y, button, 2 if args.action == "double_click" else 1, guard)
        elif args.action == "drag":
            if args.x2 is None or args.y2 is None:
                raise ToolInputError("drag needs x2/y2")
            await asyncio.to_thread(si.drag, args.x, args.y, args.x2, args.y2, 20, guard)
        else:
            await asyncio.to_thread(si.scroll, args.x, args.y, args.scroll_clicks)
        return self.ok(f"Mouse {args.action.replace('_', ' ')} at ({args.x}, {args.y})", {"x": args.x, "y": args.y})

    async def verify(self, args: MouseInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        cx, cy = si.cursor_pos()
        target = (args.x2, args.y2) if args.action == "drag" and args.x2 is not None and args.y2 is not None else (args.x, args.y)
        at_target = abs(cx - target[0]) <= 2 and abs(cy - target[1]) <= 2
        if args.action == "move":
            return VerificationResult.from_checks([Check(name="cursor at target", passed=at_target)])
        # the pointer position is checkable; whether the click did what was intended must be observed next
        return VerificationResult(verified=None, checks=[Check(name="cursor at target", passed=at_target)],
                                  note="click delivered; observe the screen/UI to confirm its effect")


# ---------------------------------------------------------------- clipboard
class ClipReadInput(ToolInput):
    max_chars: int = Field(20000, ge=1, le=500000)


class ClipboardRead(Tool):
    name = "clipboard.read"
    description = "Read text from the clipboard (privacy-classified data)."
    input_model = ClipReadInput
    capabilities = ("clipboard.read",)
    categories = ("clipboard",)
    data_class = "clipboard"
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    requires = Requires(platform="win32")

    async def run(self, args: ClipReadInput, ctx: ToolContext) -> ToolResult:
        from scar.tools.input.clipboard import get_text

        text = await asyncio.to_thread(get_text)
        if text is None:
            return self.ok("Clipboard has no text", {"text": None, "has_text": False})
        return self.ok(f"Clipboard: {len(text)} characters", {"text": text[: args.max_chars], "has_text": True,
                                                                 "length": len(text)},
                       model_view=text[: args.max_chars], source="clipboard")


class ClipWriteInput(ToolInput):
    text: str | None = None
    image_path: str | None = Field(None, description="PNG/JPG file to place on the clipboard")


class ClipboardWrite(Tool):
    name = "clipboard.write"
    description = "Put text (or an image file) on the clipboard."
    input_model = ClipWriteInput
    capabilities = ("clipboard.write",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("clipboard",)
    requires = Requires(platform="win32")
    sensitive_args = {"image_path": "path"}

    def assess(self, args: ClipWriteInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW)
        if args.image_path:
            chk = ctx.services.path_guard.check(args.image_path, PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons))
        return a

    def describe(self, args: ClipWriteInput) -> str:
        return "copy " + (f"image {args.image_path}" if args.image_path else f"{len(args.text or '')} characters") + " to the clipboard"

    async def run(self, args: ClipWriteInput, ctx: ToolContext) -> ToolResult:
        from scar.tools.input.clipboard import set_image, set_text

        if args.image_path:
            p = check_path(ctx, args.image_path, PathOp.READ).path
            await asyncio.to_thread(set_image, p)
            return self.ok(f"Copied image {p.name}", {"image": str(p)})
        if args.text is None:
            raise ToolInputError("give text or image_path")
        await asyncio.to_thread(set_text, args.text)
        return self.ok(f"Copied {len(args.text)} characters", {"length": len(args.text)})

    async def verify(self, args: ClipWriteInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        from scar.tools.input.clipboard import get_text

        if args.image_path:
            from scar.tools.input.clipboard import has_image

            return VerificationResult.from_checks([Check(name="image on clipboard", passed=await asyncio.to_thread(has_image))])
        text = await asyncio.to_thread(get_text)
        return VerificationResult.from_checks([Check(name="clipboard matches", passed=(text or "") == (args.text or ""))])


TOOLS: list[type[Tool]] = [InputType, InputHotkey, InputMouse, ClipboardRead, ClipboardWrite]
