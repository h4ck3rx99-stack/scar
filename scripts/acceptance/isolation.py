"""Acceptance runs never touch the user's real SCAR data: each run gets its own data folder (database, memory, logs,
browser profile) inside the sandbox, while configuration and downloaded models are shared read-only."""

from __future__ import annotations

import os
from pathlib import Path

from scar.config.settings import load_settings


def isolate(run_dir: Path) -> Path:
    os.environ.setdefault("SCAR_MODELS_DIR", str(load_settings().models_path))
    data = run_dir / "scar-data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["SCAR_DATA_DIR"] = str(data)
    return data
