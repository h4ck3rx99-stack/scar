"""Write non-secret settings to config.toml (shared by `scar config set` and the app API)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from scar.config import paths
from scar.config.settings import SECRET_KEYS, Settings

SECRET_MARKERS = ("password", "token", "api_key", "secret")


class SettingError(ValueError):
    pass


def is_secret_name(key: str) -> bool:
    field = key.lower().removeprefix("scar_")
    return field.upper() in SECRET_KEYS or key.upper() in SECRET_KEYS or any(m in field for m in SECRET_MARKERS)


def coerce(field: str, value: Any) -> Any:
    """Turn a CLI string (or a JSON value from the app) into the field's type."""
    if not isinstance(value, str):
        return value
    ann = str(Settings.model_fields[field].annotation)
    if "bool" in ann:
        return value.lower() in ("1", "true", "yes", "on")
    if "int" in ann and value.lstrip("-").isdigit():
        return int(value)
    if "float" in ann:
        try:
            return float(value)
        except ValueError:
            return value
    if "list" in ann:
        return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]
    return value


def set_setting(key: str, value: Any, path: Path | None = None) -> tuple[str, Any, Path]:
    """Validate and persist one setting. Returns (field, parsed value, file). Raises SettingError."""
    field = key.lower().removeprefix("scar_")
    if is_secret_name(key):
        raise SettingError("that is a secret; store it with `scar config set-secret NAME` (Windows Credential Manager)")
    if field not in Settings.model_fields:
        raise SettingError(f"unknown setting {key!r}")
    parsed = coerce(field, value)
    try:
        Settings.model_validate({**Settings().model_dump(), field: parsed})
    except Exception as exc:
        raise SettingError(f"invalid value: {exc}") from exc
    path = path or paths.config_file()
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[field] = parsed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return field, parsed, path
