"""Tool registry (C5): explicit registration, availability checks, curated exposure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from scar.tools.base import Availability, Tool

log = structlog.get_logger("scar.tools")

# role -> allowed tool-name prefixes (None = all)
ROLE_TOOLS: dict[str, tuple[str, ...] | None] = {
    "executor": None,
    "planner": ("fs.read", "fs.list", "fs.search", "fs.info", "system.", "memory.search", "memory.recall", "apps.list",
                "ask_user", "finish", "read_artifact", "windows.list", "process.list"),
    "researcher": ("web.", "browser.", "documents.read", "fs.read", "fs.list", "fs.search", "memory.", "read_artifact",
                   "finish", "ask_user"),
    "coder": ("fs.", "terminal.", "dev.", "git.", "read_artifact", "finish", "memory.", "documents.read", "code.", "ask_user"),
    "tester": ("fs.read", "fs.list", "fs.search", "terminal.", "dev.run_tests", "dev.detect", "git.status", "git.diff",
               "read_artifact", "finish", "code.run"),
    "reviewer": ("fs.read", "fs.list", "fs.search", "git.status", "git.diff", "git.log", "read_artifact", "finish"),
    "vision_analyst": ("screen.", "vision.", "ocr.", "windows.list", "uia.inspect", "read_artifact", "finish"),
    "security_reviewer": ("fs.read", "fs.list", "read_artifact", "finish"),
}


@dataclass
class ToolInfo:
    name: str
    available: bool
    prerequisite: str
    setup_doc: str
    risk: str
    categories: tuple[str, ...]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._availability: dict[str, Availability] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def refresh_availability(self) -> None:
        for name, tool in self._tools.items():
            try:
                self._availability[name] = tool.availability()
            except Exception as exc:  # noqa: BLE001 - an availability probe must never crash startup
                self._availability[name] = Availability.no(f"availability check failed: {exc}", "docs/troubleshooting.md")

    def availability(self, name: str) -> Availability:
        if name not in self._availability and name in self._tools:
            self._availability[name] = self._tools[name].availability()
        return self._availability.get(name, Availability.no("unknown tool", "docs/tools.md"))

    def available(self, role: str = "executor") -> list[Tool]:
        prefixes = ROLE_TOOLS.get(role)
        out: list[Tool] = []
        for name, tool in self._tools.items():
            if not self.availability(name).ok:
                continue
            if prefixes is not None and not tool.internal and not name.startswith(prefixes):
                continue
            out.append(tool)
        return out

    def select(self, categories: set[str] | None, role: str = "executor", limit: int = 40,
               boost: list[str] | None = None) -> list[Tool]:
        """Curated subset for the model: internal tools first, then boosted tools, then tools by category overlap."""
        tools = self.available(role)
        boost = boost or []
        if categories:
            tools = [t for t in tools if t.internal or set(t.categories) & categories or t.name in boost]

        def rank(t: Tool) -> tuple[int, int, int]:
            if t.internal:
                return (0, 0, 0)
            if t.name in boost:
                return (1, boost.index(t.name), 0)
            overlap = len(set(t.categories) & (categories or set()))
            return (2, -overlap, 0)

        return sorted(tools, key=rank)[:limit]

    def info(self) -> list[ToolInfo]:
        out: list[ToolInfo] = []
        for name, tool in sorted(self._tools.items()):
            a = self.availability(name)
            out.append(ToolInfo(name, a.ok, a.prerequisite, a.setup_doc, tool.base_risk.name, tool.categories))
        return out

    def function_schemas(self, tools: list[Tool]) -> list[dict[str, Any]]:
        """OpenAI-style function tool definitions."""
        out: list[dict[str, Any]] = []
        for t in tools:
            schema = t.schema()
            schema.pop("title", None)
            out.append({"type": "function", "function": {"name": _wire_name(t.name), "description": t.description,
                                                          "parameters": schema}})
        return out

    def by_wire_name(self, wire: str) -> Tool | None:
        return self._tools.get(wire.replace("__", "."))


def _wire_name(name: str) -> str:
    """Provider function names may not contain dots."""
    return name.replace(".", "__")


def wire_name(name: str) -> str:
    return _wire_name(name)
