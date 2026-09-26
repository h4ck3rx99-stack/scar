"""Versioned prompt templates. Bump the version suffix when a prompt changes meaningfully."""

from __future__ import annotations

from importlib import resources

PROMPT_VERSION = "v1"


def load(name: str) -> str:
    return resources.files(__package__).joinpath(f"{name}_{PROMPT_VERSION}.md").read_text(encoding="utf-8")
