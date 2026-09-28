"""Default-browser detection and the automation browser choice (Opera GX preferred, separate SCAR profile)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scar.tools.browser import default as default_mod
from scar.tools.browser import manager as manager_mod


def test_detect_channel_prefers_opera_gx_when_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(default_mod, "opera_gx_exe", lambda: r"C:\x\opera.exe")
    assert manager_mod.detect_channel("auto") == "opera"
    assert manager_mod.detect_channel("msedge") == "msedge"  # an explicit setting wins


def test_detect_channel_without_opera_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(default_mod, "opera_gx_exe", lambda: None)
    monkeypatch.setattr(manager_mod.os.path, "exists", lambda p: False)
    assert manager_mod.detect_channel("auto") == "chromium"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows install layout")
def test_opera_gx_exe_picks_newest_version_numerically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "Programs" / "Opera GX"
    for v in ("136.0.6008.67", "136.0.6008.100", "assistant"):
        (root / v).mkdir(parents=True)
        (root / v / "opera.exe").write_bytes(b"")
    (root / "opera.exe").write_bytes(b"")
    monkeypatch.setattr(default_mod, "default_browser", lambda: default_mod.Browser("Opera GXStable", "Opera GX",
                                                                                   str(root / "opera.exe")))
    assert default_mod.opera_gx_exe() == str(root / "136.0.6008.100" / "opera.exe")


class _FakeCtx:
    pages: list = []

    def on(self, *_a: object) -> None:
        pass


class _FakeChromium:
    def __init__(self, fail: set[str]) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    async def launch_persistent_context(self, _profile: str, **kw: object) -> _FakeCtx:
        self.calls.append(kw)
        which = "opera" if "executable_path" in kw else str(kw.get("channel", "chromium"))
        if which in self.fail:
            raise RuntimeError(f"{which} would not start")
        return _FakeCtx()


class _FakePw:
    def __init__(self, fail: set[str]) -> None:
        self.chromium = _FakeChromium(fail)


async def test_launch_falls_back_from_opera_to_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scar.config.settings import Settings

    s = Settings(data_dir=str(tmp_path))
    monkeypatch.setattr(default_mod, "opera_gx_exe", lambda: r"C:\x\opera.exe")
    monkeypatch.setattr(manager_mod.os.path, "exists", lambda p: p in manager_mod.EDGE_PATHS)
    m = manager_mod.BrowserManager(s)
    m._pw = _FakePw({"opera"})
    await m._launch()
    assert m.state.channel == "msedge"
    first, second = m._pw.chromium.calls
    assert first["executable_path"] == r"C:\x\opera.exe" and "channel" not in first
    assert second["channel"] == "msedge"
