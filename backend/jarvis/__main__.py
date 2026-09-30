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
    for noisy in ("httpx", "websockets", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for noisy in ("phonemizer", "pypdf", "pypdf._cmap", "pypdf._reader"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


async def ensure_llama_server(llm):
    if await llm.health():
        log.info("llama-server already running on :%d", config.LLM_PORT)
        return None
    if not config.MANAGE_LLAMA_SERVER:
        log.warning("llama-server not reachable and JARVIS_MANAGE_LLAMA_SERVER=false")
        return None
    cmd = [config.LLAMA_SERVER, "-m", config.LLM_GGUF, "-ngl", "99", "-c", "16384", "--port", str(config.LLM_PORT),
           "--host", "127.0.0.1", "--jinja", "-fa", "on", "--no-webui", "-np", "2", "--kv-unified",
           "--checkpoint-min-step", "256"]   # Qwen3.5 is hybrid: resume points every 256 tokens keep the prompt cache useful
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

    from jarvis.audio.aec import EchoCanceller, ReferenceBuffer
    aec_ref = ReferenceBuffer(out_sr=Speaker.SR) if config.ECHO_CANCEL else None
    speaker = Speaker(loop, on_level=on_level, reference=aec_ref)
    speech = SpeechOut(tts, speaker, ui)
    speech.start()
    speaker.start()

    class NoMic:
        queue = asyncio.Queue()

        def start(self):
            pass

    mic = NoMic() if text_only else Microphone(loop)

    # --- long-term memory, file index, voice ID, saved voice style
    from jarvis import runtime
    from jarvis.skills import voice_settings
    from jarvis.skills.docs import FileIndex
    from jarvis.skills.memory import MemoryDB
    runtime.tts = tts
    voice_settings.apply_saved()
    runtime.memory = MemoryDB()
    runtime.files = FileIndex()

    def index_forever():
        while True:
            try:
                runtime.files.scan()
            except Exception:  # noqa: BLE001 - indexing is best effort
                log.exception("file indexing failed")
            time.sleep(30 * 60)

    import threading
    threading.Thread(target=index_forever, daemon=True, name="file-index").start()

    def load_voiceprint():
        try:
            from jarvis.audio.speaker import VoicePrint
            vp = VoicePrint()
            vp.learn_self(tts)
            return vp
        except Exception as e:  # noqa: BLE001 - voice ID is optional
            log.warning("voice ID unavailable: %s", e)
            return None

    runtime.voiceprint = await loop.run_in_executor(None, load_voiceprint)
    if runtime.voiceprint:
        log.info("voice ID ready (%s)", "enrolled" if runtime.voiceprint.enrolled else "not enrolled yet")

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
    runtime.session = session
    if config.KWS and not text_only:
        from jarvis.audio import kws
        if kws.available():
            try:
                session.kws = await loop.run_in_executor(None, kws.NameSpotter)
            except Exception as e:  # noqa: BLE001 - the Whisper name check still works without it
                log.warning("name wake word unavailable: %s", e)
    # Scar's own settings (settings.json): hook the live objects up, then apply what was saved.
    from jarvis.core import settings
    settings.on_change("voice_volume", lambda v: setattr(speaker, "gain", float(v) / 100))
    from jarvis.skills import cliphistory
    settings.on_change("clipboard_history", lambda v: (setattr(cliphistory, "enabled", bool(v)), None if v else cliphistory.clear()))
    cliphistory.enabled = bool(settings.get("clipboard_history"))
    cliphistory.start()
    settings.apply_all()
    if aec_ref is not None:
        session.aec, session.aec_ref = EchoCanceller(), aec_ref
        session.aec.load(config.APPDATA_DIR / "aec_filter.npz")
        log.info("echo cancellation on (starting at %.0f dB)", session.aec.erle_db)
    loop.create_task(scheduler.run())
    loop.create_task(battery_watch(session))
    from jarvis.core.proactive import Proactive
    loop.create_task(Proactive(session).run())
    try:
        from jarvis.core.phone import PhoneRemote
        runtime.phone = PhoneRemote(session)
    except Exception as e:  # noqa: BLE001
        log.warning("phone remote unavailable: %s", e)
    booted = {"done": False}

    @ui.on("hello")
    async def _hello(msg):
        await ui.send("voice_state", status=session.state.value)
        await ui.send("settings", values=settings.values())
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

    @ui.on("mic")
    async def _mic(msg):
        session.mic_on = bool(msg.get("on", True))
        log.info("microphone %s (from the menu)", "on" if session.mic_on else "off")

    @ui.on("settings")
    async def _settings(msg):
        """Changed from the Settings panel on the robot's menu."""
        values = msg.get("values") or {}
        if values.get("__reset"):
            settings.reset()
            log.info("settings reset (from the panel)")
        for key, raw in values.items():
            if key not in settings.SPEC:
                continue
            val, err = settings.parse_value(key, raw) if not isinstance(raw, bool) else (raw, None)
            if err is None:
                settings.set_value(key, val)
                log.info("setting %s = %r (from the panel)", key, val)
        await ui.send("settings", values=settings.values())

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
    log.info("%s is listening. Say '%s' or 'Hey %s'.", config.NAME, config.NAME, config.NAME)
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
