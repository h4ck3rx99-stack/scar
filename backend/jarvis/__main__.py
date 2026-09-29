"""Jarvis backend: python -m jarvis   (from backend/, inside .venv)

Flags:
  --text-only   no microphone/speaker (drive it with typed commands from the UI or tests)
"""
import argparse
import asyncio
import atexit
import logging
import subprocess
import sys
import time
from pathlib import Path

import jarvis.hands.windows  # noqa: F401  (sets per-monitor DPI awareness before any window API use)
from jarvis import config
from jarvis.hands import capture

log = logging.getLogger("jarvis")


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname).1s %(name)-8s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S", handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_DIR / "jarvis.log", encoding="utf-8"),
    ])
    for noisy in ("httpx", "websockets", "faster_whisper", "phonemizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def ensure_llama_server(llm):
    if await llm.health():
        log.info("llama-server already running on :%d", config.LLM_PORT)
        return None
    if not config.MANAGE_LLAMA_SERVER:
        log.warning("llama-server not reachable and JARVIS_MANAGE_LLAMA_SERVER=false")
        return None
    cmd = [config.LLAMA_SERVER, "-m", config.LLM_GGUF, "-ngl", "99", "-c", "6144", "--port", str(config.LLM_PORT),
           "--host", "127.0.0.1", "--jinja", "-fa", "on", "--no-webui", "-np", "1"]
    if Path(config.LLM_MMPROJ).is_file():
        cmd += ["--mmproj", config.LLM_MMPROJ]  # lets the model see screenshots
    log.info("starting llama-server: %s", " ".join(cmd))
    out = open(config.LOG_DIR / "llama.txt", "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    atexit.register(proc.terminate)
    for _ in range(240):
        if await llm.health():
            log.info("llama-server ready")
            return proc
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited; see logs/llama.txt")
        await asyncio.sleep(0.5)
    raise RuntimeError("llama-server did not become ready in 2 minutes")


async def battery_watch(session):
    """Proactive: warn once when the battery drops to 20% and again at 10% while unplugged."""
    from jarvis.skills import system
    warned = set()
    while True:
        await asyncio.sleep(60)
        b = system.battery()
        if not b:
            return
        pct, plugged = b
        if plugged:
            warned.clear()
            continue
        for level in (20, 10):
            if pct <= level and level not in warned:
                warned.add(level)
                await session.announce(f"Heads up, battery is at {pct} percent. You might want to plug in.", kind="battery")
                break


async def main(text_only=False):
    from jarvis.audio.vad import SileroVAD
    from jarvis.audio.wake import WakeWord
    from jarvis.brain.agent import Agent
    from jarvis.brain.llm import LLM
    from jarvis.core.session import Session
    from jarvis.core.speech import SpeechOut
    from jarvis.core.ui import UiLink

    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    ui = UiLink()
    await ui.serve()
    log.info("websocket on ws://%s:%d", config.WS_HOST, config.WS_PORT)

    probe = LLM()
    llama_task = loop.create_task(ensure_llama_server(probe))

    def load_stt():
        from jarvis.stt.whisper_stt import WhisperSTT
        return WhisperSTT()

    def load_tts():
        from jarvis.tts.kokoro_tts import KokoroTTS
        return KokoroTTS()

    tts_f = loop.run_in_executor(None, load_tts)
    stt_f = loop.run_in_executor(None, load_stt) if not text_only else None
    vad, wake = SileroVAD(), WakeWord()
    tts = await tts_f
    stt = await stt_f if stt_f else None
    await llama_task
    await probe.aclose()
    log.info("models ready in %.1fs", time.perf_counter() - t0)

    from jarvis.audio.devices import Microphone, Speaker

    last_level = [0.0]

    def on_level(level):
        now = time.monotonic()
        if now - last_level[0] > 0.05 or level == 0:
            last_level[0] = now
            ui.emit("level", value=round(level, 3))

    speaker = Speaker(loop, on_level=on_level)
    speech = SpeechOut(tts, speaker, ui)
    speech.start()
    speaker.start()

    class NoMic:
        queue = asyncio.Queue()

        def start(self):
            pass

    mic = NoMic() if text_only else Microphone(loop)
    from jarvis.skills.reminders import Scheduler

    async def fire(item):
        if item["kind"] == "timer":
            text = f"Time's up! Your {item['text']} is done." if "timer" in item["text"] else f"Time's up: {item['text']}."
        elif item["text"] == "Alarm":
            text = "Wake up! It's " + time.strftime("%I:%M").lstrip("0") + "."
        else:
            text = f"Reminder: {item['text']}."
        await session.announce(text, kind="timer" if item["kind"] == "timer" else "reminder")

    scheduler = Scheduler(fire)
    session = Session(ui, mic, vad, wake, stt, speech, lambda control: Agent(ui, speech.say, control, scheduler))
    loop.create_task(scheduler.run())
    loop.create_task(battery_watch(session))
    booted = {"done": False}

    @ui.on("hello")
    async def _hello(msg):
        await ui.send("voice_state", status=session.state.value)
        if not booted["done"]:
            booted["done"] = True
            import random
            await ui.send("summon", entry=random.randint(0, 3), reason="boot")
            await speech.say(random.choice([f"{config.NAME} online.", "Hello! I'm here.", "Systems ready."]))

    @ui.on("avatar_clicked")
    async def _clicked(msg):
        await session.start_listening("click")

    @ui.on("text_command")
    async def _text(msg):
        await session.text_command(str(msg.get("text", ""))[:500])

    @ui.on("user_activity")
    async def _activity(msg):
        session.last_activity = time.monotonic()
        if session.state.value == "sleeping":
            await session._set(session.state.__class__.IDLE)

    loop.create_task(session.agent.warm_up())
    if ui.clients:  # the body connected while models were still loading
        await _hello(None)

    atexit.register(capture.reveal_all)
    mic.start()
    log.info("Jarvis is listening. Say 'Hey Jarvis'.")
    await session.run()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-only", action="store_true")
    args = ap.parse_args()
    setup_logging()
    try:
        asyncio.run(main(text_only=args.text_only))
    except KeyboardInterrupt:
        pass
    finally:
        capture.reveal_all()
