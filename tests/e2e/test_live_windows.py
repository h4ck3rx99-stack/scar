"""Live Windows automation (gated: SCAR_LIVE_TESTS=1). Operates only on a WinForms window this test creates."""

from __future__ import annotations

import asyncio
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.live]

TITLE = "SCAR Live Fixture"
FORM = textwrap.dedent(f"""
    Add-Type -AssemblyName System.Windows.Forms
    $f = New-Object Windows.Forms.Form
    $f.Text = '{TITLE}'; $f.Width = 700; $f.Height = 320; $f.StartPosition = 'Manual'; $f.Left = 150; $f.Top = 150
    $t = New-Object Windows.Forms.TextBox; $t.Name = 'Entry'; $t.AccessibleName = 'Entry'; $t.Width = 400; $t.Left = 20; $t.Top = 20
    $b = New-Object Windows.Forms.Button; $b.Text = 'Press Me'; $b.Left = 20; $b.Top = 70; $b.Width = 160; $b.Height = 40
    $l = New-Object Windows.Forms.Label; $l.Name = 'Status'; $l.Text = 'WAITING FOR CLICK'; $l.Left = 20; $l.Top = 140; $l.Width = 600; $l.Height = 60
    $l.Font = New-Object Drawing.Font('Segoe UI', 24)
    $b.Add_Click({{ $l.Text = 'BUTTON WAS PRESSED' }})
    $f.Controls.AddRange(@($t, $b, $l))
    $f.TopMost = $true
    [void]$f.ShowDialog()
""")


@pytest.fixture
async def fixture_window(runtime_parts, tmp_path: Path):  # type: ignore[no-untyped-def]
    from scar.tools.windows import win32
    from scar.tools.windows.uia import UiaWorker

    win32.set_dpi_awareness()
    services = runtime_parts["services"]
    services.uia = UiaWorker()
    from scar.tools.screen.ocr import OcrEngine

    services.ocr = OcrEngine()
    script = tmp_path / "form.ps1"
    script.write_text(FORM, encoding="utf-8")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA", "-File", str(script)])
    w = await asyncio.to_thread(win32.wait_for_window, lambda x: x.title == TITLE, 30)
    assert w is not None, "fixture window did not appear"
    yield w
    proc.kill()
    services.uia.shutdown()


async def test_window_focus_arrange_and_close(runtime_parts, ctx_factory, fixture_window) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("arrange the fixture window")
    obs = await p.execute("windows.focus", {"title": TITLE}, ctx)
    assert obs.result.ok and obs.result.verification.verified is True
    obs = await p.execute("windows.arrange", {"title": TITLE, "action": "move_resize", "left": 100, "top": 120,
                                              "width": 800, "height": 400}, ctx)
    assert obs.result.ok and obs.result.verification.verified is True, obs.result.summary
    obs = await p.execute("windows.arrange", {"title": TITLE, "action": "minimize"}, ctx)
    assert obs.result.verification.verified is True
    obs = await p.execute("windows.arrange", {"title": TITLE, "action": "restore"}, ctx)
    assert obs.result.verification.verified is True


async def test_uia_set_value_and_invoke(runtime_parts, ctx_factory, fixture_window) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("fill the entry and press the button in the fixture window")
    obs = await p.execute("uia.find", {"title": TITLE, "control_type": "Edit"}, ctx)
    edit_id = obs.result.data["elements"][0]["id"]
    obs = await p.execute("uia.act", {"element_id": edit_id, "action": "set_value", "value": "hello uia"}, ctx)
    assert obs.result.ok and obs.result.verification.verified is True
    obs = await p.execute("vision.click", {"target": "Press Me", "title": TITLE}, ctx)
    assert obs.result.ok and obs.result.data["method"] == "uia"
    await asyncio.sleep(0.5)
    obs = await p.execute("screen.ocr", {"target": "window", "title": TITLE}, ctx)
    assert "BUTTON WAS PRESSED" in obs.result.data["text"].upper()


async def test_keyboard_typing_with_focus_guard(runtime_parts, ctx_factory, fixture_window) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("type into the fixture window")
    obs = await p.execute("uia.find", {"title": TITLE, "control_type": "Edit"}, ctx)
    await p.execute("uia.act", {"element_id": obs.result.data["elements"][0]["id"], "action": "focus"}, ctx)
    obs = await p.execute("input.type", {"title": TITLE, "text": "typed by SCAR 123"}, ctx)
    assert obs.result.ok, obs.result.summary
    assert obs.result.verification.verified is True
    obs = await p.execute("input.hotkey", {"title": TITLE, "keys": "ctrl+a"}, ctx)
    assert obs.result.ok


async def test_mouse_click_via_ocr_grounding(runtime_parts, ctx_factory, fixture_window) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("locate the status label")
    obs = await p.execute("vision.locate", {"target": "WAITING FOR CLICK", "title": TITLE}, ctx)
    assert obs.result.ok and obs.result.data["method"] in ("uia", "ocr")
    x, y = obs.result.data["x"], obs.result.data["y"]
    obs = await p.execute("input.mouse", {"action": "move", "x": x, "y": y, "expect_hwnd": fixture_window.hwnd}, ctx)
    assert obs.result.verification.verified is True


async def test_clipboard_roundtrip(runtime_parts, ctx_factory) -> None:
    p = runtime_parts["pipeline"]
    ctx = ctx_factory("copy text")
    from scar.tools.input.clipboard import get_text, set_text

    original = get_text()
    try:
        obs = await p.execute("clipboard.write", {"text": "scar clipboard 42"}, ctx)
        assert obs.result.verification.verified is True
        obs = await p.execute("clipboard.read", {}, ctx)
        assert obs.result.data["text"] == "scar clipboard 42"
    finally:
        if original is not None:
            set_text(original)


async def test_close_window_gracefully(runtime_parts, ctx_factory, fixture_window) -> None:
    obs = await runtime_parts["pipeline"].execute("windows.close", {"title": TITLE}, ctx_factory("close the fixture window"))
    assert obs.result.ok and obs.result.verification.verified is True
