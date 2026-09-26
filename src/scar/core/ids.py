"""Time-ordered, collision-resistant identifiers."""

from __future__ import annotations

import os
import time


def new_id(prefix: str) -> str:
    """Return ``prefix_<12 hex ms timestamp><8 hex random>``; sortable by creation time."""
    ms = int(time.time() * 1000)
    return f"{prefix}_{ms:012x}{os.urandom(4).hex()}"
