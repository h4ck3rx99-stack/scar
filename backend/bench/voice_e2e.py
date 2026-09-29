"""End-to-end voice test without a human: speaks into a Virtual Audio Cable that the backend uses as its mic.

  1. start the backend with   JARVIS_INPUT_DEVICE="Line 1 (Virtual Audio Cable)"
  2. run                       .venv\\Scripts\\python bench\\voice_e2e.py

Checks: wake word + question, follow-up without wake word inside 5 s, and silence-after-5 s ignored.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import config  # noqa: E402
from jarvis.audio.devices import resolve_device  # noqa: E402
from jarvis.tts.kokoro_tts import KokoroTTS  # noqa: E402

CABLE = "Line 1 (Virtual Audio Cable)"


class Harness:
    def __init__(self):
        self.tts = KokoroTTS()
        self.tts.voice = "af_heart"          # a different voice than Jarvis's, playing "the user"
        self.out = resolve_device(CABLE, "output")
        self.events = []
        self.status = None

    def speak(self, text):
        audio, sr = self.tts.synth(text)
        audio = np.concatenate([audio, np.zeros(int(sr * 0.2), np.float32)])
        sd.play(audio * 0.9, sr, device=self.out, blocking=True)

    async def listen(self, ws):
        async for raw in ws:
            m = json.loads(raw)
            if m["event"] in ("voice_state", "say", "fly_to"):
                self.events.append((time.perf_counter(), m))
                if m["event"] == "voice_state":
                    self.status = m["status"]

    async def wait_status(self, want, timeout):
        t = time.perf_counter()
        while time.perf_counter() - t < timeout:
            if self.status in want:
                return True
            await asyncio.sleep(0.05)
        return False


async def main():
    h = Harness()
    token = config.session_token()
    async with websockets.connect(f"ws://127.0.0.1:{config.WS_PORT}") as ws:
        await ws.send(json.dumps({"v": 2, "id": "t", "event": "hello", "token": token}))
        listener = asyncio.create_task(h.listen(ws))
        await asyncio.sleep(0.5)
        results = {}
        loop = asyncio.get_running_loop()

        # 1. Wake word + question
        t0 = time.perf_counter()
        await loop.run_in_executor(None, h.speak, "Hey Jarvis, what's the capital of Japan?")
        t_end = time.perf_counter()
        ok = await h.wait_status({"follow_up"}, 25)
        heard = [m.get("transcript") for _, m in h.events if m.get("transcript")]
        said = [(round((t - t_end) * 1000), m["text"]) for t, m in h.events if m["event"] == "say" and t > t0]
        results["1_wake_question"] = {"reached_follow_up": ok, "heard": heard[-1:], "said": said}

        # 2. Follow-up inside the 5 s window, no wake word
        await asyncio.sleep(1.0)
        n = len(h.events)
        await loop.run_in_executor(None, h.speak, "And what is its population?")
        t_end = time.perf_counter()
        ok = await h.wait_status({"thinking", "speaking"}, 4)
        await h.wait_status({"follow_up"}, 25)
        heard = [m.get("transcript") for _, m in h.events[n:] if m.get("transcript")]
        said = [(round((t - t_end) * 1000), m["text"]) for t, m in h.events[n:] if m["event"] == "say"]
        results["2_follow_up_no_wake"] = {"handled": ok, "heard": heard, "said": said}

        # 3. After the window closes, speech without the wake word must be ignored
        await h.wait_status({"idle"}, 8)
        await asyncio.sleep(0.5)
        n = len(h.events)
        await loop.run_in_executor(None, h.speak, "Tell me a joke about robots.")
        await asyncio.sleep(3)
        reacted = any(m["event"] == "voice_state" and m["status"] in ("listening", "thinking") for _, m in h.events[n:])
        results["3_ignored_after_window"] = {"ignored": not reacted}

        # 4. Command via wake word -> router path latency
        n = len(h.events)
        await loop.run_in_executor(None, h.speak, "Hey Jarvis, what time is it?")
        t_end = time.perf_counter()
        await h.wait_status({"follow_up"}, 15)
        said = [(round((t - t_end) * 1000), m["text"]) for t, m in h.events[n:] if m["event"] == "say"]
        results["4_router_command"] = {"said": said}

        listener.cancel()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
