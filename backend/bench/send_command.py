"""Send typed commands to the running Jarvis, like the right-click command box does.
Usage: python bench/send_command.py "open paint" "close paint"   (waits for each to finish)"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import config  # noqa: E402


async def main(commands):
    async with websockets.connect(f"ws://127.0.0.1:{config.WS_PORT}") as ws:
        await ws.send(json.dumps({"v": 2, "id": "c0", "event": "hello", "token": config.session_token()}))
        for i, text in enumerate(commands):
            await ws.send(json.dumps({"v": 2, "id": f"c{i + 1}", "event": "text_command", "text": text}))
            print(f"> {text}")
            started = False
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if m["event"] == "say":
                    print("  jarvis:", m["text"])
                elif m["event"] == "fly_to":
                    print(f"  (flying to {m['purpose']} button at {m['target']})")
                elif m["event"] == "voice_state" and m["status"] == "thinking":
                    started = True
                elif m["event"] == "voice_state" and m["status"] in ("follow_up", "idle") and started:
                    break
            await asyncio.sleep(2.5)


asyncio.run(main(sys.argv[1:]))
