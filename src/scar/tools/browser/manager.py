"""Playwright browser manager (C9.7).

A dedicated persistent SCAR profile (never the user's real profile) running
Opera GX when installed (the user's choice), else Edge or Chrome, otherwise bundled Chromium. Tabs
get stable ids. Crashes/disconnects are detected; the next call relaunches and
restores the previously open URLs.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from scar.core.errors import CapabilityUnavailable, ToolError

log = structlog.get_logger("scar.browser")

CHROME_PATHS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def detect_channel(preferred: str) -> str:
    if preferred in ("chrome", "msedge", "chromium", "opera"):
        return preferred
    from scar.tools.browser.default import opera_gx_exe

    # the user's preferred browser (ADR 0015); SCAR always drives it with its own profile, never the user's
    if opera_gx_exe():
        return "opera"
    # Edge ships with Windows 11: no browser download, and automation stays out of the user's everyday Chrome
    if any(os.path.exists(p) for p in EDGE_PATHS):
        return "msedge"
    if any(os.path.exists(p) for p in CHROME_PATHS):
        return "chrome"
    return "chromium"


def open_in_user_browser(url: str, channel: str) -> str:
    """Open ``url`` in the user's own browser (their normal profile), detached from SCAR so it outlives this process.
    Returns the browser name used."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("only web pages are handed off")
    from scar.tools.browser.default import default_browser

    del channel  # the page goes to the user's own default browser, whichever SCAR used for automation
    os.startfile(url)  # type: ignore[attr-defined]
    return default_browser().name


@dataclass
class Tab:
    id: int
    page: Any


@dataclass
class BrowserState:
    channel: str = ""
    crashed: bool = False
    crash_count: int = 0
    last_urls: list[str] = field(default_factory=list)


class BrowserManager:
    def __init__(self, settings: Any, bus: Any = None) -> None:
        self.settings = settings
        self.bus = bus
        self._pw: Any = None
        self._ctx: Any = None
        self._tabs: dict[int, Tab] = {}
        self._active: int | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()
        self.state = BrowserState()
        self.downloads: list[dict[str, Any]] = []
        self._tab_urls: dict[int, str] = {}  # last known URL per tab, kept for crash recovery
        self.fetch_log: list[str] = []  # URLs actually loaded (for citation checks)

    @property
    def running(self) -> bool:
        return self._ctx is not None and not self.state.crashed

    async def _launch(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise CapabilityUnavailable("Playwright is not installed", "docs/browser.md") from exc
        if self._pw is None:
            self._pw = await async_playwright().start()
        channel = detect_channel(self.settings.browser_channel)
        profile: Path = self.settings.browser_profile_path
        profile.mkdir(parents=True, exist_ok=True)
        downloads: Path = self.settings.downloads_path
        downloads.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "headless": bool(self.settings.browser_headless),
            "accept_downloads": True,
            "downloads_path": str(downloads),
            "viewport": None if not self.settings.browser_headless else {"width": 1366, "height": 900},
            "args": ["--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
        }
        candidates = [channel] + [c for c in ("msedge", "chromium") if c != channel]
        errors: list[str] = []
        for channel in candidates:
            opts = dict(kwargs)
            if channel == "opera":
                from scar.tools.browser.default import opera_gx_exe

                exe = opera_gx_exe()
                if not exe:
                    continue
                opts["executable_path"] = exe
            elif channel == "msedge" and not any(os.path.exists(p) for p in EDGE_PATHS):
                continue
            elif channel != "chromium":
                opts["channel"] = channel
            try:
                self._ctx = await self._pw.chromium.launch_persistent_context(str(profile), **opts)
                break
            except Exception as exc:  # noqa: BLE001 - any launch failure means try the next channel
                log.warning("browser_channel_failed", channel=channel, error=str(exc)[:200])
                errors.append(f"{channel}: {str(exc)[:200]}")
        else:
            raise CapabilityUnavailable("cannot start a browser (for bundled Chromium run `uv run playwright install "
                                        "chromium`): " + "; ".join(errors), "docs/browser.md")
        self.state.channel = channel
        self.state.crashed = False
        self._ctx.on("close", lambda *_: self._mark_crashed("context closed"))
        self._ctx.on("page", self._on_page)
        self._tabs.clear()
        for p in self._ctx.pages:
            self._register(p)
        log.info("browser_started", channel=channel)

    def _register(self, page: Any) -> int:
        for t in self._tabs.values():
            if t.page is page:
                return t.id
        tid = self._next_id
        self._next_id += 1
        self._tabs[tid] = Tab(tid, page)
        page.on("close", lambda *_: self._on_close(tid))
        page.on("crash", lambda *_: self._mark_crashed(f"tab {tid} crashed"))
        page.on("download", self._on_download)
        page.on("framenavigated", lambda frame: self._log_nav(frame, tid))
        self._active = tid
        return tid

    def _on_close(self, tid: int) -> None:
        # a page close can mean "user closed the tab" or "the browser died"; URLs are only forgotten in close_tab()
        self._tabs.pop(tid, None)

    def _log_nav(self, frame: Any, tid: int) -> None:
        try:
            if frame.parent_frame is None and frame.url.startswith("http"):
                self._tab_urls[tid] = frame.url
                self.fetch_log.append(frame.url)
                del self.fetch_log[:-500]
        except Exception:  # noqa: BLE001 - frame may be detached
            return

    def _on_page(self, page: Any) -> None:
        self._register(page)

    def _on_download(self, download: Any) -> None:
        self.downloads.append({"url": download.url, "suggested": download.suggested_filename, "obj": download})
        del self.downloads[:-50]

    def _mark_crashed(self, why: str) -> None:
        if self._ctx is None:
            return
        if self.state.crashed:
            return
        self.state.crashed = True
        self.state.crash_count += 1
        self.state.last_urls = list(dict.fromkeys(self._tab_urls.values()))
        log.warning("browser_crashed", reason=why)

    async def ensure(self) -> None:
        async with self._lock:
            if self._ctx is not None and not self.state.crashed:
                return
            restore = list(self.state.last_urls) if self.state.crashed else []
            if self._ctx is not None:
                with contextlib.suppress(Exception):
                    await self._ctx.close()
                self._ctx = None
            await self._launch()
            self._tab_urls.clear()
            for url in restore[:5]:
                page = await self._ctx.new_page()
                with contextlib.suppress(Exception):
                    await page.goto(url, timeout=20000)
            if restore:
                log.info("browser_restored", tabs=len(restore))

    async def page(self, tab: int | None = None) -> tuple[int, Any]:
        await self.ensure()
        if tab is not None:
            t = self._tabs.get(tab)
            if t is None:
                raise ToolError(f"tab {tab} does not exist; open tabs: {sorted(self._tabs)}", "NotFound")
            self._active = tab
            return tab, t.page
        if self._active in self._tabs:
            return self._active, self._tabs[self._active].page  # type: ignore[index]
        if self._tabs:
            tid = max(self._tabs)
            self._active = tid
            return tid, self._tabs[tid].page
        page = await self._ctx.new_page()
        return self._register(page), page

    async def new_tab(self) -> tuple[int, Any]:
        await self.ensure()
        page = await self._ctx.new_page()
        return self._register(page), page

    async def close_tab(self, tab: int) -> None:
        t = self._tabs.get(tab)
        if t is None:
            raise ToolError(f"tab {tab} does not exist", "NotFound")
        await t.page.close()
        self._tabs.pop(tab, None)
        self._tab_urls.pop(tab, None)
        if self._active == tab:
            self._active = max(self._tabs) if self._tabs else None

    async def tabs(self) -> list[dict[str, Any]]:
        out = []
        for tid, t in sorted(self._tabs.items()):
            try:
                title = await t.page.title()
            except Exception:  # noqa: BLE001
                title = ""
            out.append({"id": tid, "url": t.page.url, "title": title, "active": tid == self._active})
        return out

    async def close(self) -> None:
        async with self._lock:
            if self._ctx is not None:
                with contextlib.suppress(Exception):
                    await self._ctx.close()
                self._ctx = None
            if self._pw is not None:
                with contextlib.suppress(Exception):
                    await self._pw.stop()
                self._pw = None
            self._tabs.clear()
