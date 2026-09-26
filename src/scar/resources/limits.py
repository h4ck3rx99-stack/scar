"""Background-task policy presets (C7)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_subprocesses: int
    max_browser_contexts: int
    max_agents: int
    max_model_calls: int
    max_background_tasks: int
    max_monitors: int
    monitor_min_interval_s: float
    resource_sample_interval_s: float
    max_ocr_per_minute: int
    max_vision_per_minute: int


PRESETS: dict[str, Limits] = {
    "minimal": Limits(2, 1, 1, 1, 1, 5, 30.0, 20.0, 10, 4),
    "balanced": Limits(4, 2, 3, 2, 3, 20, 5.0, 10.0, 30, 10),
    "performance": Limits(8, 4, 5, 4, 6, 50, 1.0, 5.0, 120, 30),
}


def limits_for(policy: str) -> Limits:
    return PRESETS.get(policy, PRESETS["balanced"])
