"""Doctor reports a dependency whose native library fails to load instead of crashing."""

from __future__ import annotations

import importlib

import pytest

from scar.cli.doctor import Doctor
from scar.config.settings import Settings


def test_native_library_load_failure_is_a_failed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.import_module

    def fake(name: str, *a, **k):  # type: ignore[no-untyped-def]
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake)
    d = Doctor(Settings(), services=None)
    d.dependencies()
    broken = [c for c in d.results if "native library failed to load" in c.label]
    assert len(broken) == 1 and broken[0].ok is False and "PortAudio" in broken[0].label
