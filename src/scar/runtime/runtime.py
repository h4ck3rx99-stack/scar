"""Composition root (B4): one asyncio runtime owns the agent, tools, scheduler, monitors, voice and model lifecycle.

Startup: settings → logging → services → subsystems → registry/pipeline → task manager → interrupted-task report
→ scheduler/monitor re-arm → kill switch. Shutdown (in order): cancel tasks, persist state, stop managed model
servers, kill managed child process trees, close the browser SCAR created.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

import structlog

from scar.agent.runner import TaskManager
from scar.config.settings import Settings
from scar.core.events import TaskProgress
from scar.memory.store import MemoryStore
from scar.observability.logging import configure_logging
from scar.runtime.builder import build_services
from scar.runtime.registration import build_registry
from scar.runtime.services import Services
from scar.tools.pipeline import ToolPipeline
from scar.tools.registry import ToolRegistry

log = structlog.get_logger("scar.runtime")


class Runtime:
    def __init__(self, settings: Settings, *, console_logs: bool = False) -> None:
        self.settings = settings
        self.log_file = configure_logging(settings.log_path, settings.log_level, console=console_logs or settings.debug,
                                          debug=settings.debug)
        self.services: Services = build_services(settings)
        self.registry: ToolRegistry | None = None
        self.pipeline: ToolPipeline | None = None
        self.tasks: TaskManager | None = None
        self.startup_notes: list[str] = []
        self._started = False

    # ------------------------------------------------------------------ lifecycle
    async def start(self, *, with_hotkeys: bool = True, with_scheduler: bool = True) -> None:
        if self._started:
            return
        s = self.services
        if sys.platform == "win32":
            from scar.tools.windows.win32 import set_dpi_awareness

            mode = set_dpi_awareness()
            log.info("dpi_awareness", mode=mode)
        if s.db.recovered_from_corruption:
            self.startup_notes.append(s.db.recovered_from_corruption)
        self._attach_subsystems()
        self.registry = build_registry(s)
        self.pipeline = ToolPipeline(s, self.registry)
        self.tasks = TaskManager(s, self.registry, self.pipeline)
        s.tasks = self.tasks
        s.extras.setdefault("cwd", os.getcwd())
        interrupted = self.tasks.mark_interrupted()
        for r in interrupted:
            self.startup_notes.append(f"Interrupted when SCAR stopped: {r['objective'][:80]}")
        if with_scheduler:
            s.scheduler.start()
            for r in s.scheduler.missed_on_start:
                self.startup_notes.append(f"Missed while off: {r['text'][:80]}")
            self.startup_notes += s.monitors.rearm()
        self._register_killswitch()
        if with_hotkeys and not s.killswitch.start() and s.killswitch.last_error:
            self.startup_notes.append(f"Kill-switch hotkey unavailable: {s.killswitch.last_error}")
        s.artifacts.enforce_retention()
        s.tracer.prune()
        s.metrics.prune()
        self._started = True
        log.info("runtime_started", session=s.session_id, tools=len(self.registry.all()))

    def _attach_subsystems(self) -> None:
        s = self.services
        s.memory = MemoryStore(s.db, s.embeddings)
        from scar.tools.apps.index import AppIndex
        from scar.tools.dev.devserver import DevServerManager
        from scar.tools.dev.github import GitHubClient
        from scar.tools.monitor.service import MonitorService
        from scar.tools.notify.service import Notifier
        from scar.tools.scheduler.service import Scheduler

        s.app_index = AppIndex(s.db)
        s.notifier = Notifier(s)
        s.scheduler = Scheduler(s)
        s.monitors = MonitorService(s)
        s.devservers = DevServerManager(s)
        s.github = GitHubClient(s.secrets)
        if sys.platform == "win32":
            from scar.tools.screen.ocr import OcrEngine
            from scar.tools.windows.uia import UiaWorker

            s.uia = UiaWorker()
            s.ocr = OcrEngine()
        from scar.tools.browser.manager import BrowserManager

        s.browser = BrowserManager(s.settings, s.bus)
        self._attach_integrations()

    def _attach_integrations(self) -> None:
        """Communication integrations (email, messaging, calendar, contacts). Missing credentials surface later as
        CapabilityUnavailable from the tools, never as startup failures."""
        s = self.services
        try:
            from scar.runtime.integrations import attach

            attach(s)
        except ImportError as exc:
            self.startup_notes.append(f"communication integrations not loaded: {exc}")

    def _register_killswitch(self) -> None:
        s = self.services
        ks = s.killswitch

        def cancel_tasks() -> None:
            if self.tasks is not None:
                self.tasks.cancel_all("kill switch")

        def kill_children() -> None:
            s.processes.kill_all()

        def stop_tts() -> None:
            if s.voice is not None:
                s.voice.stop_speaking()

        ks.register("halt input", lambda: None)  # INPUT_GATE is halted by trigger() itself
        ks.register("cancel tasks", cancel_tasks)
        ks.register("kill child processes", kill_children)
        ks.register("stop speech", stop_tts)
        ks.register("announce", lambda: s.bus.publish(TaskProgress(message="Emergency stop: all actions halted.")))

    async def stop(self) -> None:
        s = self.services
        if self.tasks is not None:
            await self.tasks.shutdown()
        with contextlib.suppress(Exception):
            if s.scheduler is not None:
                await s.scheduler.stop()
        with contextlib.suppress(Exception):
            if s.monitors is not None:
                await s.monitors.stop_all()
        with contextlib.suppress(Exception):
            if s.voice is not None:
                await s.voice.stop()
        with contextlib.suppress(Exception):
            if s.devservers is not None:
                await s.devservers.stop_all()
        await s.local_models.shutdown()
        s.processes.kill_all()
        with contextlib.suppress(Exception):
            if s.browser is not None:
                await s.browser.close()
        with contextlib.suppress(Exception):
            await s.router.aclose()
            await s.stt.aclose()
        s.killswitch.stop()
        if s.uia is not None:
            s.uia.shutdown()
        if s.command_guard.ps_parser is not None:
            s.command_guard.ps_parser.close()
        s.metrics.flush()
        s.db.close()
        self._started = False
        log.info("runtime_stopped")

    # ------------------------------------------------------------------ convenience
    async def run_objective(self, objective: str, **kwargs: Any) -> Any:
        assert self.tasks is not None
        return await self.tasks.run(objective, **kwargs)

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


async def run_until_cancelled(rt: Runtime, stop: asyncio.Event) -> None:
    await stop.wait()
