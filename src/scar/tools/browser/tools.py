"""Browser automation tools (C9.7). Page content is UNTRUSTED_EXTERNAL."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from scar.core.errors import ToolError, ToolInputError
from scar.core.types import Check, RiskLevel, SideEffect, ToolResult, TrustLevel, VerificationResult
from scar.security.path_guard import PathOp
from scar.security.risk import RiskAssessment
from scar.tools.base import Requires, Tool, ToolContext, ToolInput
from scar.tools.fs.common import check_path, sha256_file

BROWSER = Requires(setting="browser_enabled", modules=("playwright",), setup_doc="docs/browser.md")
PLAYWRIGHT_TIMEOUT = 15000


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class TargetSpec(ToolInput):
    role: str | None = Field(None, description="ARIA role: button, link, textbox, checkbox, combobox, heading, …")
    name: str | None = Field(None, description="accessible name used with role")
    text: str | None = Field(None, description="visible text")
    label: str | None = Field(None, description="form field label")
    placeholder: str | None = None
    selector: str | None = Field(None, description="CSS selector (last resort)")
    index: int | None = Field(None, ge=0, description="which match when several match (0-based)")
    tab: int | None = None


async def resolve_locator(page: Any, t: TargetSpec) -> Any:
    if t.selector:
        loc = page.locator(t.selector)
    elif t.role:
        loc = page.get_by_role(t.role, name=t.name) if t.name else page.get_by_role(t.role)
    elif t.label:
        loc = page.get_by_label(t.label)
    elif t.placeholder:
        loc = page.get_by_placeholder(t.placeholder)
    elif t.text:
        loc = page.get_by_text(t.text)
    else:
        raise ToolInputError("describe the element with role/name, text, label, placeholder or selector")
    count = await loc.count()
    if count == 0 and t.text and not t.selector:
        loc = page.get_by_text(t.text, exact=False)
        count = await loc.count()
    if count == 0 and t.name and t.role:
        loc = page.get_by_role(t.role, name=re.compile(re.escape(t.name), re.IGNORECASE))
        count = await loc.count()
    if count == 0:
        raise ToolError("no element matches that description on the page", "NotFound")
    if count > 1:
        if t.index is not None:
            if t.index >= count:
                raise ToolError(f"only {count} matches; index {t.index} is out of range", "NotFound")
            return loc.nth(t.index)
        visible = [i for i in range(min(count, 20)) if await loc.nth(i).is_visible()]
        if len(visible) == 1:
            return loc.nth(visible[0])
        samples = []
        for i in range(min(count, 6)):
            try:
                samples.append(f"{i}: {(await loc.nth(i).inner_text(timeout=1000))[:50]!r}")
            except Exception:  # noqa: BLE001
                samples.append(f"{i}: ?")
        raise ToolError(f"{count} elements match — give index. " + "; ".join(samples), "Ambiguous")
    return loc.first


def _pw_error(exc: Exception) -> ToolError:
    msg = str(exc).splitlines()[0][:300]
    return ToolError(f"browser: {msg}", "BrowserError")


class OpenInput(ToolInput):
    url: str = Field(description="URL to load (https:// added if missing)")
    new_tab: bool = False
    tab: int | None = None
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "load"


class BrowserOpen(Tool):
    name = "browser.open"
    description = "Open a URL in SCAR's browser (optionally in a new tab). Verifies the URL and load state."
    input_model = OpenInput
    capabilities = ("browser.navigate",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser", "web")
    requires = BROWSER
    timeout = 90.0
    sensitive_args = {"url": "url"}
    resource_slot = "browser"

    def assess(self, args: OpenInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        url = _normalize_url(args.url)
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https", "about", "file"):
            a.raise_to(RiskLevel.HIGH, f"{scheme}: URLs can launch other programs")
        if scheme == "file":
            chk = ctx.services.path_guard.check(urlparse(url).path.lstrip("/"), PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
        a.facts.domains.append(_domain(url))
        return a

    def describe(self, args: OpenInput) -> str:
        return f"open {_normalize_url(args.url)} in the browser"

    def progress_line(self, args: OpenInput) -> str | None:
        return f"Opening {_domain(_normalize_url(args.url)) or args.url}."

    async def run(self, args: OpenInput, ctx: ToolContext) -> ToolResult:
        bm = ctx.services.browser
        url = _normalize_url(args.url)
        tid, page = await (bm.new_tab() if args.new_tab else bm.page(args.tab))
        try:
            resp = await page.goto(url, wait_until=args.wait_until, timeout=45000)
        except Exception as exc:
            raise _pw_error(exc) from exc
        title = await page.title()
        status = resp.status if resp is not None else None
        return self.ok(f"Opened {title or page.url}", {"tab": tid, "url": page.url, "title": title, "status": status,
                                                         "requested_url": url, "channel": bm.state.channel},
                       source=f"browser:{page.url}")

    async def verify(self, args: OpenInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        bm = ctx.services.browser
        _tid, page = await bm.page(result.data["tab"])
        want = urlparse(result.data["requested_url"])
        got = urlparse(page.url)
        state_ok = await page.evaluate("document.readyState") in ("interactive", "complete")
        checks = [Check(name="page loaded", passed=state_ok),
                  Check(name="URL host matches", passed=(got.hostname or "") == (want.hostname or "") or want.scheme == "about",
                        detail=page.url[:120])]
        status = result.data.get("status")
        if status is not None:
            checks.append(Check(name="HTTP status < 400", passed=int(status) < 400, detail=str(status)))
        return VerificationResult.from_checks(checks, {"url": page.url})


def _normalize_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url) and not re.match(r"^[a-zA-Z]:\\", url):
        return url
    if url.startswith(("localhost", "127.0.0.1")):
        return "http://" + url
    return "https://" + url


class NavInput(ToolInput):
    action: Literal["back", "forward", "reload"]
    tab: int | None = None


class BrowserNavigate(Tool):
    name = "browser.navigate"
    description = "Go back, forward or reload in a tab."
    input_model = NavInput
    capabilities = ("browser.navigate",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER

    async def run(self, args: NavInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        try:
            if args.action == "back":
                await page.go_back(timeout=PLAYWRIGHT_TIMEOUT)
            elif args.action == "forward":
                await page.go_forward(timeout=PLAYWRIGHT_TIMEOUT)
            else:
                await page.reload(timeout=30000)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"{args.action.title()}: {page.url}", {"tab": tid, "url": page.url})


class TabsInput(ToolInput):
    action: Literal["list", "switch", "close", "new"] = "list"
    tab: int | None = None


class BrowserTabs(Tool):
    name = "browser.tabs"
    description = "List, switch to, open or close browser tabs."
    input_model = TabsInput
    capabilities = ("browser.navigate",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: TabsInput, ctx: ToolContext) -> ToolResult:
        bm = ctx.services.browser
        if args.action == "new":
            await bm.new_tab()
        elif args.action in ("switch", "close"):
            if args.tab is None:
                raise ToolInputError("give tab")
            if args.action == "close":
                await bm.close_tab(args.tab)
            else:
                _, page = await bm.page(args.tab)
                await page.bring_to_front()
        else:
            await bm.ensure()
        tabs = await bm.tabs()
        return self.ok(f"{len(tabs)} tab(s)", {"tabs": tabs},
                       model_view="\n".join(f"{'*' if t['active'] else ' '}[{t['id']}] {t['title'][:60]} — {t['url']}" for t in tabs))


class ClickInput(TargetSpec):
    button: Literal["left", "right", "middle"] = "left"
    double: bool = False


class BrowserClick(Tool):
    name = "browser.click"
    description = "Click an element on the page, located by role+name, text, label, placeholder or CSS selector."
    input_model = ClickInput
    capabilities = ("browser.interact",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER
    timeout = 60.0
    resource_slot = "browser"

    def assess(self, args: ClickInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        label = " ".join(x for x in (args.name, args.text, args.label, args.selector) if x).lower()
        if re.search(r"\b(submit|send|post|publish|delete|remove|confirm|sign up|register|apply|comment)\b", label):
            a.raise_to(RiskLevel.HIGH, f"'{label[:40]}' may submit or change something")
        if re.search(r"\b(buy|pay|purchase|place order|checkout|subscribe|donate|transfer)\b", label):
            a.raise_to(RiskLevel.CRITICAL, "looks like a purchase or payment")
        bm = ctx.services.browser
        if bm is not None and bm.running:
            try:
                page = bm._tabs[args.tab or bm._active].page
                a.facts.domains.append(_domain(page.url))
            except (KeyError, AttributeError, TypeError):
                pass
        return a

    def describe(self, args: ClickInput) -> str:
        what = args.name or args.text or args.label or args.placeholder or args.selector
        return f"click '{what}' in the browser"

    async def run(self, args: ClickInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        before = page.url
        loc = await resolve_locator(page, args)
        try:
            await loc.scroll_into_view_if_needed(timeout=5000)
            if args.double:
                await loc.dblclick(button=args.button, timeout=PLAYWRIGHT_TIMEOUT)
            else:
                await loc.click(button=args.button, timeout=PLAYWRIGHT_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"Clicked; now at {page.url}", {"tab": tid, "url_before": before, "url": page.url,
                                                       "title": await page.title()})

    async def verify(self, args: ClickInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        return VerificationResult(verified=None, evidence={"url": result.data["url"]},
                                  note="click delivered; check the page state for its effect")


_CREDENTIAL = re.compile(r"(?i)password|passwd|pin\b|otp|one-time|2fa|verification code|cvv|cvc|security code")
_PAYMENT = re.compile(r"(?i)card ?number|cc-number|cc-csc|cc-exp|expir|iban|routing|account number|billing")


class TypeInput(TargetSpec):
    value: str = Field(description="text to enter (replaces the field's content)")
    submit: bool = Field(False, description="press Enter afterwards")
    sensitive_field: bool = Field(False, description="true for password/security-code/payment fields (always CRITICAL)")


class BrowserType(Tool):
    name = "browser.type"
    description = "Fill a text field on the page (replaces its content); optionally press Enter to submit."
    input_model = TypeInput
    capabilities = ("browser.interact",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER
    sensitive_args = {"value": "body"}
    resource_slot = "browser"

    def assess(self, args: TypeInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM)
        if args.sensitive_field:
            a.raise_to(RiskLevel.CRITICAL, "entering a password, security code or payment data")
        hint = " ".join(x for x in (args.name, args.label, args.placeholder, args.selector) if x)
        if _CREDENTIAL.search(hint) or (args.selector and "type=password" in args.selector.replace(" ", "").replace('"', "")):
            a.raise_to(RiskLevel.CRITICAL, "entering a password or security code")
        if _PAYMENT.search(hint) or re.fullmatch(r"[\d \-]{13,23}", args.value.strip()):
            a.raise_to(RiskLevel.CRITICAL, "entering payment data")
        from scar.security.redaction import global_redactor

        if global_redactor().contains_secret(args.value):
            a.raise_to(RiskLevel.CRITICAL, "the text looks like a secret")
        if args.submit:
            a.raise_to(RiskLevel.HIGH, "submits the form")
        return a

    def describe(self, args: TypeInput) -> str:
        field_name = args.label or args.name or args.placeholder or args.selector or args.text
        shown = args.value if len(args.value) < 80 else args.value[:77] + "…"
        return f"type '{shown}' into '{field_name}'" + (" and submit" if args.submit else "")

    async def run(self, args: TypeInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        loc = await resolve_locator(page, args)
        try:
            input_type = (await loc.get_attribute("type", timeout=3000) or "").lower()
            autocomplete = (await loc.get_attribute("autocomplete", timeout=3000) or "").lower()
        except Exception as exc:
            if not args.sensitive_field:
                # fail closed: a field that cannot be inspected might be a password or card field
                raise ToolError("could not inspect the field to check whether it is a password or payment field; "
                                "describe it more precisely, or retry with sensitive_field=true (requires the user's "
                                "typed confirmation)", "FieldUninspectable") from exc
            input_type, autocomplete = "", ""
        sensitive = input_type == "password" or autocomplete.startswith("cc-") or autocomplete in (
            "current-password", "new-password", "one-time-code")
        if sensitive and not args.sensitive_field:
            # the description did not reveal it: refuse, so the retry (sensitive_field=true) is CRITICAL-approved
            raise ToolError("this is a password/payment field; retry with sensitive_field=true, which requires the "
                            "user's typed confirmation", "SensitiveField")
        try:
            await loc.fill(args.value, timeout=PLAYWRIGHT_TIMEOUT)
            if args.submit:
                await loc.press("Enter", timeout=PLAYWRIGHT_TIMEOUT)
                await page.wait_for_load_state("domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok("Entered text" + (" and submitted" if args.submit else ""), {"tab": tid, "url": page.url,
                                                                                    "submitted": args.submit})

    async def verify(self, args: TypeInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        if args.submit:
            return VerificationResult(verified=None, note="form submitted; check the resulting page")
        _tid, page = await ctx.services.browser.page(result.data["tab"])
        try:
            loc = await resolve_locator(page, args)
            val = await loc.input_value(timeout=3000)
        except (ToolError, Exception):  # noqa: BLE001
            return VerificationResult.unverifiable("field no longer readable")
        return VerificationResult.from_checks([Check(name="field value set", passed=val == args.value)])


class PressInput(ToolInput):
    key: str = Field(description="e.g. Enter, Escape, ArrowDown, Control+L")
    tab: int | None = None


class BrowserPress(Tool):
    name = "browser.press"
    description = "Press a key in the page (Enter, Escape, ArrowDown, Control+A, …)."
    input_model = PressInput
    capabilities = ("browser.interact",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER

    async def run(self, args: PressInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        try:
            await page.keyboard.press(args.key)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"Pressed {args.key}", {"tab": tid, "url": page.url})


class SelectInput(TargetSpec):
    option: str = Field(description="option label or value")


class BrowserSelect(Tool):
    name = "browser.select"
    description = "Choose an option in a <select> dropdown."
    input_model = SelectInput
    capabilities = ("browser.interact",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER

    async def run(self, args: SelectInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        loc = await resolve_locator(page, args)
        try:
            try:
                chosen = await loc.select_option(label=args.option, timeout=5000)
            except Exception:  # noqa: BLE001 - fall back to value match
                chosen = await loc.select_option(value=args.option, timeout=5000)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"Selected {args.option}", {"tab": tid, "selected": chosen})


class ScrollInput(ToolInput):
    direction: Literal["down", "up", "top", "bottom"] = "down"
    pages: float = Field(1.0, gt=0, le=20)
    tab: int | None = None


class BrowserScroll(Tool):
    name = "browser.scroll"
    description = "Scroll the page."
    input_model = ScrollInput
    capabilities = ("browser.interact",)
    categories = ("browser",)
    requires = BROWSER
    side_effects = SideEffect.NONE

    async def run(self, args: ScrollInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        js = {"top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0,document.body.scrollHeight)",
              "down": f"window.scrollBy(0, window.innerHeight*{args.pages})",
              "up": f"window.scrollBy(0, -window.innerHeight*{args.pages})"}[args.direction]
        await page.evaluate(js)
        y = await page.evaluate("window.scrollY")
        return self.ok(f"Scrolled {args.direction}", {"tab": tid, "scroll_y": y})


class ExtractInput(ToolInput):
    mode: Literal["main_text", "all_text", "links", "tables", "selector", "snapshot"] = "main_text"
    selector: str | None = None
    tab: int | None = None
    max_chars: int = Field(20000, ge=100, le=200000)


class BrowserExtract(Tool):
    name = "browser.extract"
    description = ("Read the current page: main article text, all text, links, tables, a CSS selector's text, or the "
                   "accessibility snapshot (roles/names — best for deciding what to click).")
    input_model = ExtractInput
    capabilities = ("browser.read",)
    categories = ("browser", "web")
    requires = BROWSER
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL
    timeout = 60.0

    async def run(self, args: ExtractInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        url = page.url
        data: dict[str, Any] = {"tab": tid, "url": url, "title": await page.title()}
        try:
            if args.mode == "snapshot":
                text = await page.locator("body").aria_snapshot(timeout=PLAYWRIGHT_TIMEOUT)
            elif args.mode == "main_text":
                html = await page.content()
                from scar.tools.web.extract import extract_main

                text = await asyncio.to_thread(extract_main, html, url) or await page.inner_text("body", timeout=PLAYWRIGHT_TIMEOUT)
            elif args.mode == "all_text":
                text = await page.inner_text("body", timeout=PLAYWRIGHT_TIMEOUT)
            elif args.mode == "links":
                links = await page.eval_on_selector_all(
                    "a[href]", "els => els.slice(0, 400).map(a => ({text: (a.innerText||'').trim().slice(0,120), href: a.href}))")
                data["links"] = links
                text = "\n".join(f"[{i}] {lk['text']} -> {lk['href']}" for i, lk in enumerate(links))
            elif args.mode == "tables":
                tables = await page.eval_on_selector_all(
                    "table", "ts => ts.slice(0, 10).map(t => Array.from(t.rows).slice(0, 200).map(r => Array.from(r.cells).map(c => c.innerText.trim())))")
                data["tables"] = tables
                text = "\n\n".join("\n".join(" | ".join(row) for row in t) for t in tables)
            else:
                if not args.selector:
                    raise ToolInputError("mode=selector needs selector")
                text = "\n".join(await page.locator(args.selector).all_inner_texts())
        except ToolInputError:
            raise
        except Exception as exc:
            raise _pw_error(exc) from exc
        text = text[: args.max_chars]
        data["text"] = text
        return self.ok(f"Read {len(text)} characters from {data['title'][:60] or url}", data, model_view=text,
                       source=f"web:{url}")


class ScreenshotInput(ToolInput):
    full_page: bool = False
    tab: int | None = None


class BrowserScreenshot(Tool):
    name = "browser.screenshot"
    description = "Screenshot the current page (viewport or full page) as a PNG artifact."
    input_model = ScreenshotInput
    capabilities = ("browser.read",)
    categories = ("browser",)
    requires = BROWSER
    data_class = "screen"

    async def run(self, args: ScreenshotInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        png = await page.screenshot(full_page=args.full_page, timeout=30000)
        info = ctx.services.artifacts.put_bytes(ctx.task_id, png, ".png")
        return self.ok(f"Page screenshot saved ({len(png) // 1024} KB)", {"tab": tid, "artifact": info.ref, "path": info.path,
                                                                        "url": page.url})


class WaitInput(ToolInput):
    text: str | None = None
    selector: str | None = None
    url_contains: str | None = None
    load_state: Literal["load", "domcontentloaded", "networkidle"] | None = None
    timeout_s: float = Field(20, gt=0, le=120)
    tab: int | None = None


class BrowserWait(Tool):
    name = "browser.wait"
    description = "Wait until text/selector appears, the URL contains something, or a load state is reached."
    input_model = WaitInput
    capabilities = ("browser.read",)
    categories = ("browser",)
    requires = BROWSER
    timeout = 130.0
    timeout_field = "timeout_s"

    async def run(self, args: WaitInput, ctx: ToolContext) -> ToolResult:
        tid, page = await ctx.services.browser.page(args.tab)
        ms = int(args.timeout_s * 1000)
        try:
            if args.text:
                await page.get_by_text(args.text).first.wait_for(timeout=ms)
            if args.selector:
                await page.wait_for_selector(args.selector, timeout=ms)
            if args.url_contains:
                await page.wait_for_url(re.compile(re.escape(args.url_contains)), timeout=ms)
            if args.load_state:
                await page.wait_for_load_state(args.load_state, timeout=ms)
        except Exception as exc:
            raise ToolError(f"condition not met within {args.timeout_s:.0f}s", "Timeout") from exc
        return self.ok("Condition met", {"tab": tid, "url": page.url})


class DownloadInput(TargetSpec):
    url: str | None = Field(None, description="download this URL directly (instead of clicking an element)")


class BrowserDownload(Tool):
    name = "browser.download"
    description = ("Download a file by clicking a link/button or from a URL. Saved to the downloads folder; never "
                   "executed automatically.")
    input_model = DownloadInput
    capabilities = ("browser.download",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser", "web")
    requires = BROWSER
    timeout = 600.0
    sensitive_args = {"url": "url"}

    def assess(self, args: DownloadInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM, ["downloads a file (it will not be run)"])
        if args.url:
            a.facts.domains.append(_domain(args.url))
        return a

    def describe(self, args: DownloadInput) -> str:
        return f"download {args.url or (args.name or args.text or args.selector)}"

    async def run(self, args: DownloadInput, ctx: ToolContext) -> ToolResult:
        bm = ctx.services.browser
        tid, page = await bm.page(args.tab)
        try:
            async with page.expect_download(timeout=120000) as dl_info:
                if args.url:
                    await page.evaluate("u => { const a = document.createElement('a'); a.href = u; a.download = ''; "
                                        "document.body.appendChild(a); a.click(); a.remove(); }", args.url)
                else:
                    loc = await resolve_locator(page, args)
                    await loc.click(timeout=PLAYWRIGHT_TIMEOUT)
            download = await dl_info.value
            dest_dir: Path = ctx.services.settings.downloads_path
            dest_dir.mkdir(parents=True, exist_ok=True)
            name = Path(download.suggested_filename).name or "download.bin"
            dest = dest_dir / name
            n = 1
            while dest.exists():
                dest = dest_dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            await download.save_as(str(dest))
        except ToolError:
            raise
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"Downloaded {dest.name}", {"tab": tid, "path": str(dest), "url": download.url,
                                                   "size": dest.stat().st_size, "sha256": sha256_file(dest)})

    async def verify(self, args: DownloadInput, result: ToolResult, ctx: ToolContext) -> VerificationResult:
        p = Path(result.data["path"])
        return VerificationResult.from_checks([Check(name="file saved", passed=p.is_file() and p.stat().st_size > 0),
                                               Check(name="hash matches", passed=sha256_file(p) == result.data["sha256"])])


class UploadInput(TargetSpec):
    files: list[str] = Field(min_length=1, max_length=20)


class BrowserUpload(Tool):
    name = "browser.upload"
    description = "Attach local files (from allowed folders) to a file input on the page."
    input_model = UploadInput
    capabilities = ("browser.upload",)
    base_risk = RiskLevel.MEDIUM
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER
    sensitive_args = {"files": "attachment"}
    data_class = "files"

    def assess(self, args: UploadInput, ctx: ToolContext) -> RiskAssessment:
        a = RiskAssessment(RiskLevel.MEDIUM, ["gives local files to a web page"])
        for f in args.files:
            chk = ctx.services.path_guard.check(f, PathOp.READ)
            if chk.denied:
                a.deny("; ".join(chk.reasons), "secret_paths")
            a.raise_to(chk.risk, "")
            a.facts.paths.append(chk.canonical)
        a.facts.external_destination = True
        return a

    def describe(self, args: UploadInput) -> str:
        return f"upload {', '.join(Path(f).name for f in args.files)} to the page"

    async def run(self, args: UploadInput, ctx: ToolContext) -> ToolResult:
        paths = [str(check_path(ctx, f, PathOp.READ).path) for f in args.files]
        for p in paths:
            if not Path(p).is_file():
                raise ToolError(f"not a file: {p}", "NotFound")
        tid, page = await ctx.services.browser.page(args.tab)
        loc = await resolve_locator(page, args)
        try:
            await loc.set_input_files(paths, timeout=PLAYWRIGHT_TIMEOUT)
        except Exception as exc:
            raise _pw_error(exc) from exc
        return self.ok(f"Attached {len(paths)} file(s)", {"tab": tid, "files": paths})


class StateInput(ToolInput):
    tab: int | None = None


class BrowserState(Tool):
    name = "browser.state"
    description = "Current browser state: URL, title, tabs, recent downloads, browser channel."
    input_model = StateInput
    capabilities = ("browser.read",)
    categories = ("browser",)
    requires = BROWSER
    output_trust = TrustLevel.UNTRUSTED_EXTERNAL

    async def run(self, args: StateInput, ctx: ToolContext) -> ToolResult:
        bm = ctx.services.browser
        tid, page = await bm.page(args.tab)
        tabs = await bm.tabs()
        return self.ok(f"At {page.url}", {"tab": tid, "url": page.url, "title": await page.title(), "tabs": tabs,
                                          "downloads": [{"url": d["url"], "file": d["suggested"]} for d in bm.downloads[-10:]],
                                          "channel": bm.state.channel, "crashes": bm.state.crash_count})


class CloseInput(ToolInput):
    pass


class BrowserClose(Tool):
    name = "browser.close"
    description = "Close SCAR's browser window (all its tabs)."
    input_model = CloseInput
    capabilities = ("browser.navigate",)
    base_risk = RiskLevel.LOW
    side_effects = SideEffect.LOCAL
    categories = ("browser",)
    requires = BROWSER

    async def run(self, args: CloseInput, ctx: ToolContext) -> ToolResult:
        await ctx.services.browser.close()
        return self.ok("Browser closed", {"closed": True})


TOOLS: list[type[Tool]] = [BrowserOpen, BrowserNavigate, BrowserTabs, BrowserClick, BrowserType, BrowserPress, BrowserSelect,
                           BrowserScroll, BrowserExtract, BrowserScreenshot, BrowserWait, BrowserDownload, BrowserUpload,
                           BrowserState, BrowserClose]
