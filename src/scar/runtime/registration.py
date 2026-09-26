"""Explicit tool registration: one line per tool module (each exports ``TOOLS``)."""

from __future__ import annotations

import importlib

import structlog

from scar.tools.registry import ToolRegistry

log = structlog.get_logger("scar.registration")

TOOL_MODULES: list[str] = [
    "scar.tools.internal",
    "scar.tools.fs.tools",
    "scar.tools.terminal.tools",
    "scar.tools.system.tools",
    "scar.tools.process.tools",
    "scar.tools.windows.tools",
    "scar.tools.apps.tools",
    "scar.tools.input.tools",
    "scar.tools.screen.tools",
    "scar.tools.browser.tools",
    "scar.tools.web.tools",
    "scar.tools.documents.tools",
    "scar.tools.dev.tools",
    "scar.tools.dev.devserver_tools",
    "scar.tools.dev.github",
    "scar.tools.memory.tools",
    "scar.tools.scheduler.tools",
    "scar.tools.contacts.tools",
    "scar.tools.email.tools",
    "scar.tools.messaging.tools",
    "scar.tools.calendar.tools",
    "scar.agent.subagents",
]


def build_registry(services: object, modules: list[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    for mod_name in modules or TOOL_MODULES:
        module = importlib.import_module(mod_name)
        for tool_cls in module.TOOLS:
            registry.register(tool_cls(services))
    registry.refresh_availability()
    return registry
