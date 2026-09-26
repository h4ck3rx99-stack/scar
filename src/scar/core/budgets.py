"""Task budgets and loop prevention (C8.6)."""

from __future__ import annotations

import time
from collections import Counter, deque

from pydantic import BaseModel

from scar.core.errors import BudgetExceeded


class Budget(BaseModel):
    max_steps: int = 40
    max_retries_per_step: int = 3
    max_wall_seconds: float = 900.0
    max_tokens: int = 400_000
    max_identical_failures: int = 3
    no_progress_window: int = 6


TASK_CLASS_WALL_SECONDS: dict[str, float] = {
    "fast": 60.0,
    "conversation": 120.0,
    "standard": 900.0,
    "development": 1800.0,
    "research": 1200.0,
    "background": 24 * 3600.0,
}


class BudgetTracker:
    """Tracks steps, tokens, time and repeated failures for one task."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self.started = time.monotonic()
        self.steps = 0
        self.tokens = 0
        self._failures: Counter[str] = Counter()
        self._recent_states: deque[str] = deque(maxlen=max(2, budget.no_progress_window))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def step(self) -> None:
        self.steps += 1
        if self.steps > self.budget.max_steps:
            raise BudgetExceeded("steps", self.budget.max_steps)
        self.check_time()

    def check_time(self) -> None:
        if self.elapsed > self.budget.max_wall_seconds:
            raise BudgetExceeded("wall_seconds", self.budget.max_wall_seconds)

    def add_tokens(self, n: int) -> None:
        self.tokens += n
        if self.tokens > self.budget.max_tokens:
            raise BudgetExceeded("tokens", self.budget.max_tokens)

    def record_failure(self, action_hash: str) -> int:
        """Record a failed action; return how many times this exact action failed."""
        self._failures[action_hash] += 1
        return self._failures[action_hash]

    def repeated_failure(self, action_hash: str) -> bool:
        return self._failures[action_hash] >= self.budget.max_identical_failures

    def record_state(self, fingerprint: str) -> None:
        self._recent_states.append(fingerprint)

    def no_progress(self) -> bool:
        """True when the last N step fingerprints are identical (nothing changed)."""
        window = self.budget.no_progress_window
        if len(self._recent_states) < window:
            return False
        return len(set(self._recent_states)) == 1
