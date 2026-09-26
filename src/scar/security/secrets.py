"""Secret resolution: environment variables, ``.env``, and Windows Credential Manager.

Secrets never live in config.toml, never enter model context, and every value
resolved here is registered with the global redactor.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

from scar.config.settings import SECRET_KEYS
from scar.security.redaction import global_redactor

KEYRING_SERVICE = "SCAR"

# Additional secret names that are not provider API keys.
EXTRA_SECRET_NAMES: frozenset[str] = frozenset(
    {
        "CLOUDFLARE_ACCOUNT_ID",
        "AZURE_SPEECH_REGION",
        "TELEGRAM_API_ID",
        "SCAR_IPC_TOKEN",
    }
)


def _keyring_get(name: str) -> str | None:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except (KeyringError, RuntimeError, OSError):
        return None


class SecretStore:
    def __init__(self, dotenv_path: Path | None = None) -> None:
        self._dotenv_path = dotenv_path or Path.cwd() / ".env"
        self._dotenv: dict[str, str] = {}
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._cache.clear()
            self._dotenv = {}
            if self._dotenv_path.exists():
                self._dotenv = {k: v for k, v in dotenv_values(self._dotenv_path).items() if v is not None}

    def _raw(self, name: str) -> str | None:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
        value = os.environ.get(name) or self._dotenv.get(name) or _keyring_get(name)
        if value is not None:
            value = value.strip() or None
        with self._lock:
            self._cache[name] = value
        if value and (name in SECRET_KEYS or name.endswith(("_KEY", "_TOKEN", "_SECRET", "_HASH", "_PASSWORD"))):
            global_redactor().add_secret(name, value)
        return value

    def get(self, name: str) -> SecretStr | None:
        value = self._raw(name)
        return SecretStr(value) if value else None

    def get_plain(self, name: str) -> str | None:
        """Non-secret companion values (region, account id, API id)."""
        return self._raw(name)

    def has(self, name: str) -> bool:
        return bool(self._raw(name))

    def source_of(self, name: str) -> str | None:
        if os.environ.get(name):
            return "environment"
        if self._dotenv.get(name):
            return ".env"
        if _keyring_get(name):
            return "credential manager"
        return None

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, name, value)
        global_redactor().add_secret(name, value)
        with self._lock:
            self._cache[name] = value

    def delete(self, name: str) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except PasswordDeleteError:
            return False
        with self._lock:
            self._cache.pop(name, None)
        return True

    def preload_for_redaction(self) -> int:
        """Resolve every known secret name so its value is registered with the redactor."""
        count = 0
        for name in sorted(SECRET_KEYS | EXTRA_SECRET_NAMES):
            if self._raw(name):
                count += 1
        return count
