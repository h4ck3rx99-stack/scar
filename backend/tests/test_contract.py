"""Every message the backend actually sends, and every message the frontend is allowed to send, must match
contracts/protocol.json. This is what stops the three layers drifting apart again."""
import asyncio
import json
import re
from pathlib import Path

import jsonschema
import pytest

from jarvis.core.ui import UiLink

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "protocol.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))


def backend_messages():
    async def run():
        ui = UiLink.__new__(UiLink)
        ui.clients, ui._pending, ui.proxy_waiters = set(), {}, {}
        ws = FakeWS()
        ui.clients.add(ws)
        b = {"x": 1, "y": 2, "width": 300, "height": 200}
        await ui.send("voice_state", status="follow_up", follow_up_ms_left=4200)
        await ui.send("voice_state", status="thinking", transcript="minimize chrome")
        await ui.send("say", text="Minimizing Chrome.")
        await ui.send("level", value=0.42)
        await ui.send("summon", entry=2, reason="wake")
        await ui.send("proxy_start", window_id="0x1a", texture_url="http://127.0.0.1:8765/cap/a.webp", bounds=b, target=b,
                      grab={"x": 5, "y": 6}, duration_ms=900, effect="close")
        await ui.send("proxy_end", window_id="0x1a", final_bounds=b)
        await ui.send("theme", mode="light")
        await ui.send("fly_to", target={"x": 1800, "y": 12}, purpose="minimize", duration_ms=550, button=b)
        return ws.sent
    return asyncio.run(run())


FRONTEND = [
    {"v": 2, "id": "a", "event": "hello", "token": "t", "screens": []},
    {"v": 2, "id": "b", "event": "destination_reached", "reply_to": "x"},
    {"v": 2, "id": "c", "event": "proxy_arrived", "window_id": "0x1a"},
    {"v": 2, "id": "g", "event": "proxy_ready", "window_id": "0x1a"},
    {"v": 2, "id": "d", "event": "avatar_clicked"},
    {"v": 2, "id": "e", "event": "text_command", "text": "open youtube"},
    {"v": 2, "id": "f", "event": "user_activity"},
]


@pytest.mark.parametrize("msg", backend_messages() + FRONTEND, ids=lambda m: m["event"])
def test_message_matches_schema(msg):
    VALIDATOR.validate(msg)


def test_bad_messages_rejected():
    for bad in ({"v": 2, "id": "x", "event": "voice_state", "status": "dancing"},
                {"v": 1, "id": "x", "event": "say", "text": "hi"},
                {"v": 2, "id": "x", "event": "fly_to", "target": {"x": 1}, "purpose": "minimize"}):
        with pytest.raises(jsonschema.ValidationError):
            VALIDATOR.validate(bad)


def test_code_only_uses_contract_events():
    """Scan backend + frontend sources for event names and check each one is in the contract."""
    known = set(SCHEMA["x-direction"]["backend_to_frontend"]) | set(SCHEMA["x-direction"]["frontend_to_backend"])
    backend = ROOT / "backend" / "jarvis"
    used = set()
    for f in backend.rglob("*.py"):
        used |= set(re.findall(r"""\.(?:send|emit)\(\s*["'](\w+)["']""", f.read_text(encoding="utf-8")))
    frontend = ROOT / "frontend" / "src"
    for f in frontend.rglob("*.js"):
        used |= set(re.findall(r"""bridge\.send\(\s*["'](\w+)["']""", f.read_text(encoding="utf-8")))
    assert used, "scanner found no events"
    assert used <= known, f"events missing from contract: {used - known}"
