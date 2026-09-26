"""Filesystem locations for SCAR, resolved with platformdirs."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

APP = "SCAR"


def config_dir() -> Path:
    """%APPDATA%\\SCAR on Windows."""
    override = os.environ.get("SCAR_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir(APP, appauthor=False, roaming=True))


def config_file() -> Path:
    return config_dir() / "config.toml"


def user_policy_file() -> Path:
    return config_dir() / "policy.yaml"


def user_providers_file() -> Path:
    return config_dir() / "providers.yaml"


def default_data_dir() -> Path:
    """%LOCALAPPDATA%\\SCAR on Windows."""
    return Path(user_data_dir(APP, appauthor=False, roaming=False))


def default_log_dir() -> Path:
    base = Path(user_log_dir(APP, appauthor=False))
    # platformdirs on Windows yields ...\SCAR\Logs; normalise to the documented lower-case path
    return base.parent / "logs" if base.name.lower() == "logs" else base


def package_dir() -> Path:
    return Path(__file__).resolve().parent
