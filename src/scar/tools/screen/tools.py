"""Screen capture, OCR, screen understanding and grounded clicking (C9.6)."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path
from scar.tools.screen import capture as cap
from scar.tools.vision.service import Mark, VisionService, marks_from_ocr, marks_from_uia, to_png
from scar.tools.windows import win32
from scar.tools.windows.tools import WindowTarget, resolve_window

SCREEN = Requires(platform="win32", setup_doc="docs/troubleshooting.md#screen")


class CaptureTarget(ToolInput):
    target: Literal["foreground", "screen", "monitor", "window", "region"] = "foreground"
    monitor: int | None = None
    hwnd: int | None = None
    title: str | None = None
    process: str | None = None
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None


def do_capture(t: CaptureTarget) -> cap.Capture:
    if t.target == "screen":
        return cap.capture_screen()
    if t.target == "monitor":
        return cap.capture_monitor(t.monitor or 0)
    if t.target == "window":
        w = resolve_window(WindowTarget(hwnd=t.hwnd, title=t.title, process=t.process))
        return cap.capture_window(w.hwnd)
    if t.target == "region":
        if None in (t.left, t.top, t.width, t.height):
            raise ToolInputError("region needs left, top, width, height")
        return cap.capture_region(int(t.left or 0), int(t.top or 0), int(t.width or 0), int(t.height or 0))
    return cap.foreground_capture()


async def _ocr(ctx: ToolContext, image: Image.Image) -> Any:
    ctx.services.admission.admit_ocr()
    return await ctx.services.ocr.recognize(image)


class CaptureInput(CaptureTarget):
    pass


class ScreenCapture(Tool):
    name = "screen.capture"
    description = "Take a screenshot (foreground window by default, or screen/monitor/window/region). Saved as a PNG artifact."
    input_model = CaptureInput
    capabilities = ("screen.read",)
    categories = ("screen",)
    requires = SCREEN
    data_class = "screen"

    async def run(self, args: CaptureInput, ctx: ToolContext) -> ToolResult:
        c = await asyncio.to_thread(do_capture, args)
        info = ctx.services.artifacts.put_bytes(ctx.task_id, to_png(c.image), ".png")
        c.image.close()
        return self.ok(f"Screenshot of {c.window_title or c.source} saved", {"artifact": info.ref, "path": info.path,
                       "left": c.left, "top": c.top, "width": c.image.width, "height": c.image.height, "source": c.source,
                       "window_title": c.window_title})


class OcrInput(CaptureTarget):
    image_path: str | None = Field(None, description="OCR an image file instead of the screen")


class ScreenOcr(Tool):
    name = "screen.ocr"
    description = "Read text on screen (or in an image file) with Windows OCR; returns lines with screen coordinates."
    input_model = OcrInput
    capabilities = ("screen.read",)
    categories = ("screen", "documents")
    requires = SCREEN
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "screen"
    sensitive_args = {"image_path": "path"}

    def assess(self, args: OcrInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW)
        if args.image_path:
            chk = ctx.services.path_guard.check(args.image_path, PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
            a.raise_to(chk.risk, "")
        return a

    async def run(self, args: OcrInput, ctx: ToolContext) -> ToolResult:
        if args.image_path:
            p = check_path(ctx, args.image_path, PathOp.READ).path
            img = Image.open(p)
            origin, source = (0, 0), f"image:{p}"
        else:
            c = await asyncio.to_thread(do_capture, args)
            img, origin, source = c.image, (c.left, c.top), c.window_title or c.source
        res = await _ocr(ctx, img)
        img.close()
        lines = [{"text": ln.text, "x": ln.x + origin[0], "y": ln.y + origin[1], "w": ln.w, "h": ln.h} for ln in res.lines]
        return self.ok(f"Read {len(lines)} lines of text from {source}", {"text": res.text, "lines": lines, "source": source},
                       model_view=res.text or "(no text found)", source=f"ocr:{source}")


class DescribeInput(ToolInput):
    question: str = Field("What is on the screen?", description="what to find out")
    target: Literal["foreground", "screen", "window"] = "foreground"
    hwnd: int | None = None
    title: str | None = None
    use_vision: Literal["auto", "never", "always"] = "auto"


class ScreenDescribe(Tool):
    name = "screen.describe"
    description = ("Understand what is on screen: foreground app/window, UI elements, visible text (UIA + OCR), and — "
                   "only when needed — a vision model. Use for 'look at my screen', 'what does this error say'.")
    input_model = DescribeInput
    capabilities = ("screen.read",)
    categories = ("screen", "vision")
    requires = SCREEN
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "screen"
    timeout = 180.0

    async def run(self, args: DescribeInput, ctx: ToolContext) -> ToolResult:
        t = CaptureTarget(target=args.target if args.target != "window" else "window", hwnd=args.hwnd, title=args.title)
        c = await asyncio.to_thread(do_capture, t)
        hwnd = win32.foreground_hwnd() if args.target == "foreground" else (args.hwnd or None)
        win = win32.window_info(hwnd) if hwnd and win32.window_exists(hwnd) else None
        res = await _ocr(ctx, c.image)
        uia_summary = ""
        if ctx.services.uia is not None and hwnd:
            try:
                uia = ctx.services.uia
                elems = await uia.call(lambda: uia.inspect_sync(hwnd, 5, 120))
                uia_summary = "\n".join(e.line() for e in elems[:120])
            except (ToolError, OSError):
                uia_summary = ""
        facts = [f"Foreground app: {win.process} — window '{win.title}'" if win else f"Captured: {c.source}"]
        context = "\n".join(facts) + "\nVisible text (OCR):\n" + (res.text[:4000] or "(none)")
        vision_answer = None
        want_vision = args.use_vision == "always" or (
            args.use_vision == "auto" and (len(res.text.strip()) < 20 or _needs_visual(args.question)))
        vision_error = None
        if want_vision:
            try:
                ctx.services.admission.admit_vision()
                vision_answer = await VisionService(ctx.services).ask(c.image, args.question, ctx.task, context=context)
            except Exception as exc:  # noqa: BLE001 - vision is an optional escalation; OCR/UIA result still stands
                vision_error = str(exc)
        c.image.close()
        parts = [context]
        if uia_summary:
            parts.append("UI elements:\n" + uia_summary[:3000])
        if vision_answer:
            parts.append("Vision model:\n" + vision_answer)
        elif vision_error:
            parts.append(f"(vision unavailable: {vision_error[:200]})")
        data = {"app": win.process if win else None, "window_title": win.title if win else None, "ocr_text": res.text,
                "vision": vision_answer, "vision_error": vision_error,
                "method": "uia+ocr+vision" if vision_answer else "uia+ocr"}
        summary = f"Looking at {win.process} — {win.title[:60]}" if win else "Captured the screen"
        return self.ok(summary, data, model_view="\n\n".join(parts), source=f"screen:{win.title if win else 'screen'}")


def _needs_visual(q: str) -> bool:
    ql = q.lower()
    return any(w in ql for w in ("color", "colour", "blue", "red", "green", "icon", "image", "picture", "look like", "layout",
                                 "chart", "graph", "photo", "logo", "button should", "where is", "which button", "highlighted"))


class VisionAskInput(ToolInput):
    question: str
    image_path: str | None = Field(None, description="image file; default is the foreground window")
    artifact: str | None = Field(None, description="artifact:// screenshot from screen.capture")


class VisionAsk(Tool):
    name = "vision.ask"
    description = "Ask a vision model about an image or the current window (charts, icons, colours, layout)."
    input_model = VisionAskInput
    capabilities = ("vision.analyze",)
    categories = ("vision", "screen")
    requires = SCREEN
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    data_class = "screen"
    timeout = 180.0
    sensitive_args = {"image_path": "path"}

    def assess(self, args: VisionAskInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.LOW)
        if args.image_path:
            chk = ctx.services.path_guard.check(args.image_path, PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
        return a

    async def run(self, args: VisionAskInput, ctx: ToolContext) -> ToolResult:
        ctx.services.admission.admit_vision()
        if args.artifact:
            img = Image.open(ctx.services.artifacts.resolve(args.artifact))
        elif args.image_path:
            img = Image.open(check_path(ctx, args.image_path, PathOp.READ).path)
        else:
            img = (await asyncio.to_thread(cap.foreground_capture)).image
        answer = await VisionService(ctx.services).ask(img, args.question, ctx.task)
        img.close()
        route = ctx.services.router.last_route.get("vision", "")
        return self.ok("Vision answer ready", {"answer": answer, "model": route}, model_view=answer, source="vision")


class LocateInput(ToolInput):
    target: str = Field(description="what to find, e.g. 'the blue Save button', 'OK', 'search box'")
    hwnd: int | None = None
    title: str | None = None
    process: str | None = None
    allow_vision: bool = True


async def locate(args: LocateInput, ctx: ToolContext) -> dict[str, Any]:
    """Cheapest-first grounding: UIA name match → OCR text match → set-of-marks vision."""
    if args.hwnd or args.title or args.process:
        w = await asyncio.to_thread(resolve_window, WindowTarget(hwnd=args.hwnd, title=args.title, process=args.process))
    else:
        hwnd = win32.foreground_hwnd()
        if not hwnd:
            raise ToolError("no foreground window", "NotFound")
        w = win32.window_info(hwnd)
    bounds = (w.left, w.top, w.right, w.bottom)
    target = args.target.strip().strip("'\"")
    full = " ".join(target.lower().split())
    variants = list(dict.fromkeys([full, _core_label(target)]))
    elements: list[dict[str, Any]] = []
    uia = ctx.services.uia
    if uia is not None:
        try:
            infos = await uia.call(lambda: uia.inspect_sync(w.hwnd, 14, 1500))
            elements = [e.as_dict() for e in infos]
        except (ToolError, OSError):
            elements = []
    # 1. UIA exact/near name match on actionable controls
    best: tuple[float, dict[str, Any]] | None = None
    for e in elements:
        if e.get("offscreen") or not e.get("name"):
            continue
        name_l = e["name"].lower()
        score = max(difflib.SequenceMatcher(None, v, name_l).ratio() for v in variants)
        if name_l in variants:
            score = 1.0
        if "Invoke" in e.get("patterns", []) or e["control_type"] in ("ButtonControl", "HyperlinkControl", "MenuItemControl"):
            score += 0.05
        if best is None or score > best[0]:
            best = (score, e)
    if best is not None and best[0] >= 0.9:
        e = best[1]
        return {"method": "uia", "element_id": e["id"], "x": (e["left"] + e["right"]) // 2, "y": (e["top"] + e["bottom"]) // 2,
                "label": f"{e['control_type']} '{e['name']}'", "confidence": min(1.0, best[0]), "hwnd": w.hwnd}
    # 2. OCR text match
    c = await asyncio.to_thread(cap.capture_window, w.hwnd)
    res = await _ocr(ctx, c.image)
    for ln in res.lines:
        text = ln.text.lower()
        hit = next((v for v in variants if v and (v == text or (len(v) >= 3 and v in text and len(text) <= len(v) + 12))), None)
        if hit:
            words = [wd for wd in ln.words if wd.text.lower() in hit.split()] or ln.words
            x0 = min(wd.x for wd in words)
            x1 = max(wd.x + wd.w for wd in words)
            y0 = min(wd.y for wd in words)
            y1 = max(wd.y + wd.h for wd in words)
            c.image.close()
            return {"method": "ocr", "x": c.left + (x0 + x1) // 2, "y": c.top + (y0 + y1) // 2, "label": f"text '{ln.text}'",
                    "confidence": 0.85, "hwnd": w.hwnd}
    # 3. set-of-marks vision
    if not args.allow_vision:
        c.image.close()
        raise ToolError(f"could not find '{target}' via UI Automation or OCR", "NotFound")
    ctx.services.admission.admit_vision()
    marks: list[Mark] = marks_from_uia(elements, bounds)
    marks += marks_from_ocr([{"text": ln.text, "x": ln.x, "y": ln.y, "w": ln.w, "h": ln.h} for ln in res.lines],
                            (c.left, c.top), len(marks) + 1)
    if not marks:
        c.image.close()
        raise ToolError("no candidate elements detected on screen", "NotFound")
    chosen, conf, reason = await VisionService(ctx.services).choose_mark(c.image, marks, (c.left, c.top), target, ctx.task)
    c.image.close()
    if chosen is None or conf < 0.35:
        raise ToolError(f"the vision model could not confidently find '{target}' ({reason[:120]})", "NotFound")
    x, y = chosen.center
    return {"method": "set-of-marks", "mark": chosen.id, "x": x, "y": y, "label": chosen.label, "confidence": conf,
            "hwnd": w.hwnd, "reason": reason[:200]}


def _core_label(target: str) -> str:
    """'click the blue Save button' -> 'blue save'; leading verbs and trailing element nouns only."""
    import re as _re

    t = " ".join(target.lower().split())
    t = _re.sub(r"^(please\s+)?(click|press|tap|select|choose|hit|open)\s+(on\s+)?", "", t)
    t = _re.sub(r"^the\s+", "", t)
    t = _re.sub(r"\s+(button|link|tab|menu item|menu|icon|field|box|checkbox|option)$", "", t)
    return t.strip() or " ".join(target.lower().split())


class VisionLocate(Tool):
    name = "vision.locate"
    description = ("Find an on-screen element from a description (UIA → OCR → set-of-marks vision). Returns screen "
                   "coordinates and, for UIA matches, an element id for uia.act. Does not click.")
    input_model = LocateInput
    capabilities = ("screen.read",)
    categories = ("vision", "screen", "input")
    requires = SCREEN
    data_class = "screen"
    timeout = 180.0

    async def run(self, args: LocateInput, ctx: ToolContext) -> ToolResult:
        found = await locate(args, ctx)
        return self.ok(f"Found {found['label']} at ({found['x']}, {found['y']}) via {found['method']}", found)


class ClickTargetInput(LocateInput):
    button: Literal["left", "right", "double"] = "left"


class VisionClick(Tool):
    name = "vision.click"
    description = ("Click an on-screen element described in words ('click the blue Submit button'). Grounds via UIA, "
                   "OCR or set-of-marks vision, prefers UIA Invoke, and keeps focus safety.")
    input_model = ClickTargetInput
    capabilities = ("input.mouse",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("vision", "input", "screen")
    requires = Requires(platform="win32", setting="pc_control_enabled")
    timeout = 240.0

    def assess(self, args: ClickTargetInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        label = args.target.lower()
        if any(w in label for w in ("send", "delete", "submit", "publish", "confirm", "post", "remove", "uninstall")):
            a.raise_to(RiskLevel.HIGH, f"clicking '{args.target[:40]}' may be irreversible")
        if any(w in label for w in ("pay", "buy", "purchase", "order", "checkout", "transfer")):
            a.raise_to(RiskLevel.CRITICAL, "looks like a financial action")
        return a

    def describe(self, args: ClickTargetInput) -> str:
        return f"{args.button}-click '{args.target}'" + (f" in {args.title or args.process}" if args.title or args.process else "")

    async def run(self, args: ClickTargetInput, ctx: ToolContext) -> ToolResult:
        from scar.tools.input import sendinput as si

        found = await locate(args, ctx)
        hwnd = found["hwnd"]
        if not await asyncio.to_thread(win32.focus_window, hwnd):
            raise ToolError("could not focus the target window", "FocusRefused")
        guard = si.focus_guard(hwnd)
        if found["method"] == "uia" and args.button == "left":
            uia = ctx.services.uia
            try:
                await uia.call(lambda: uia.act_sync(found["element_id"], "invoke"))
                found["via"] = "uia-invoke"
                return self.ok(f"Clicked {found['label']}", found)
            except ToolError:
                pass
        count = 2 if args.button == "double" else 1
        await asyncio.to_thread(si.click, found["x"], found["y"], "right" if args.button == "right" else "left", count, guard)
        found["via"] = "mouse"
        return self.ok(f"Clicked {found['label']}", found)

    async def verify(self, args: ClickTargetInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult(verified=None, checks=[Check(name="target located", passed=True,
                                                               detail=f"{result.data.get('method')} {result.data.get('label')}")],
                                  note="click delivered; confirm the effect by observing the screen")


def load_image(path: Path) -> Image.Image:
    return Image.open(path)


TOOLS: list[type[Tool]] = [ScreenCapture, ScreenOcr, ScreenDescribe, VisionAsk, VisionLocate, VisionClick]
