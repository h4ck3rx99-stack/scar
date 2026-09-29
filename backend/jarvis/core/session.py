"""The voice session state machine (the "Alexa feel").

 IDLE ──wake word / avatar click──► LISTENING ──500 ms silence──► THINKING ──first audio──► SPEAKING
   ▲                                   │ no speech 4 s                                          │ playback drained
   │                                   ▼                                                        ▼ +250 ms
   └───────── 5 s no speech ───────── FOLLOW_UP ◄──────────────────────────────────────────────┘
                                       │ speech onset (no wake word needed)
                                       └────────────────────────────► LISTENING
 Wake word during THINKING/SPEAKING = barge-in. Idle for SLEEP_AFTER_S = SLEEPING (avatar hides at the bezel).

Echo protection: while the assistant talks, only the wake word is listened for (VAD is ignored), and the
follow-up window opens only after the speaker has actually gone quiet, so Jarvis never answers itself.
"""
import asyncio
import collections
import csv
import datetime
import logging
import random
import re
import time
from enum import Enum

import numpy as np

from jarvis import config

log = logging.getLogger("session")
BLOCK_MS = config.BLOCK * 1000 // config.SAMPLE_RATE  # 32

# Whisper's classic hallucinations on noise/silence.
_JUNK = {"", "you", "thank you", "thanks", "bye", "thank you for watching", "thanks for watching", "okay", "hmm", "uh", "um", "so", "the"}
_WAKE_PREFIX = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|a)\W+)?(?:jarvis|jervis|jarvi|jarves|travis|vis)\b\W*", re.I)


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"
    SLEEPING = "sleeping"


def strip_wake(text: str) -> str:
    return _WAKE_PREFIX.sub("", text.strip()).strip()


class Session:
    def __init__(self, ui, mic, vad, wake, stt, speech, agent_factory):
        self.ui, self.mic, self.vad, self.wake, self.stt, self.speech = ui, mic, vad, wake, stt, speech
        self.agent = agent_factory(self._control)
        self.state = State.IDLE
        self.preroll = collections.deque(maxlen=int(config.PREROLL_S * 1000 / BLOCK_MS))
        self.utter = []
        self.listen_started = 0.0
        self.speech_ms = self.silence_ms = 0
        self.speech_started = False
        self.onset_ms = 0
        self.follow_deadline = 0.0
        self.last_follow_emit = 0.0
        self.last_activity = time.monotonic()
        self.wake_cooldown_until = 0.0
        self.turn_task = None
        self.timings = {}
        self.stt_pool = None
        config.LOG_DIR.mkdir(exist_ok=True)
        self.latency_csv = config.LOG_DIR / "latency.csv"
        self.speech.on_first_audio = self._first_audio

    # ------------------------------------------------------------ state changes
    async def _set(self, state, **extra):
        if state == self.state and not extra:
            return
        self.state = state
        log.info("state -> %s %s", state.value, extra or "")
        await self.ui.send("voice_state", status=state.value, **extra)

    async def start_listening(self, reason, preroll_blocks=0):
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
        self.speech.cancel()
        was_sleeping = self.state == State.SLEEPING
        self.utter = list(self.preroll)[-preroll_blocks:] if preroll_blocks else []
        self.listen_started = time.monotonic()
        self.speech_ms = self.silence_ms = 0
        # "Hey Jarvis, minimize Chrome" in one breath: the user is usually still talking when the wake fires.
        self.speech_started = reason in ("follow_up",)
        self.last_activity = time.monotonic()
        self.timings = {"trigger": reason, "t_listen": time.perf_counter()}
        if was_sleeping or reason == "boot":
            await self.ui.send("summon", entry=random.randint(0, 3), reason=reason)
        await self._set(State.LISTENING)

    async def _control(self, name):
        if name == "stop":
            self.speech.cancel()
            self.timings["no_follow_up"] = True
        elif name == "go_to_sleep":
            await self.speech.say(random.choice(["Going to sleep.", "Call me if you need me.", "I'll be around."]))
            self.timings["sleep_after"] = True

    # --------------------------------------------------------------- audio loop
    async def run(self):
        while True:
            block = await self.mic.queue.get()
            try:
                await self._process(block)
            except Exception:  # noqa: BLE001 - never let one bad block kill the ears
                log.exception("audio block failed")

    async def _process(self, block):
        now = time.monotonic()
        self.preroll.append(block)
        prob = self.vad(block)
        woke = False
        if now >= self.wake_cooldown_until:
            woke = self.wake.feed((block * 32767).astype(np.int16))
            if woke:
                self.wake.reset()
                self.wake_cooldown_until = now + 1.5
                log.info("wake word (%.2f)", self.wake.last_score)

        st = self.state
        if st in (State.IDLE, State.SLEEPING):
            if woke:
                await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))
            elif st == State.IDLE and now - self.last_activity > config.SLEEP_AFTER_S:
                await self._set(State.SLEEPING)

        elif st == State.LISTENING:
            self.utter.append(block)
            if prob >= config.VAD_ON:
                self.speech_ms += BLOCK_MS
                self.silence_ms = 0
                if self.speech_ms >= config.SPEECH_ONSET_MS:
                    self.speech_started = True
            elif prob < config.VAD_OFF and self.speech_started:
                self.silence_ms += BLOCK_MS
            elapsed_ms = (now - self.listen_started) * 1000
            if self.speech_started and self.silence_ms >= config.END_SILENCE_MS:
                await self._finish_utterance()
            elif elapsed_ms > config.MAX_UTTERANCE_MS:
                await self._finish_utterance()
            elif not self.speech_started and elapsed_ms > config.NO_SPEECH_TIMEOUT_MS:
                log.info("no speech after trigger")
                self.last_activity = now
                await self._set(State.IDLE)

        elif st == State.FOLLOW_UP:
            if woke:
                await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))
                return
            self.onset_ms = self.onset_ms + BLOCK_MS if prob >= config.VAD_ON else 0
            if self.onset_ms >= config.SPEECH_ONSET_MS:
                # Keep ~0.5 s before the onset so the first syllable isn't clipped.
                await self.start_listening("follow_up", preroll_blocks=int(500 / BLOCK_MS))
                return
            left = (self.follow_deadline - now) * 1000
            if left <= 0:
                self.last_activity = now
                await self._set(State.IDLE)
            elif now - self.last_follow_emit >= 0.25:
                self.last_follow_emit = now
                await self.ui.send("voice_state", status="follow_up", follow_up_ms_left=int(left))

        elif st in (State.THINKING, State.SPEAKING) and woke:
            log.info("barge-in")
            await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))

    async def _finish_utterance(self):
        audio = np.concatenate(self.utter) if self.utter else np.zeros(0, np.float32)
        self.utter = []
        self.timings["t_end_speech"] = time.perf_counter()
        await self._set(State.THINKING)
        self.turn_task = asyncio.get_running_loop().create_task(self._turn(audio=audio))

    async def text_command(self, text):
        """Typed command from the UI: same pipeline, minus the ears."""
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
        self.speech.cancel()
        self.timings = {"trigger": "text", "t_end_speech": time.perf_counter()}
        await self._set(State.THINKING)
        self.turn_task = asyncio.get_running_loop().create_task(self._turn(text=text))

    # ------------------------------------------------------------------ a turn
    def _first_audio(self, t):
        self.timings.setdefault("t_first_audio", t)
        if self.state == State.THINKING:
            asyncio.get_running_loop().create_task(self._set(State.SPEAKING))

    async def _turn(self, audio=None, text=None):
        try:
            if text is None:
                if len(audio) < config.SAMPLE_RATE * 0.3 or float(np.sqrt(np.mean(audio ** 2))) < 0.003:
                    await self._after_turn(empty=True)
                    return
                loop = asyncio.get_running_loop()
                text = await loop.run_in_executor(None, self.stt.transcribe, audio)
                self.timings["t_stt"] = time.perf_counter()
            text = strip_wake(text)
            norm = re.sub(r"[^\w\s']", "", text.lower()).strip()
            log.info("heard: %r", text)
            if norm in _JUNK:
                await self._after_turn(empty=True)
                return
            await self.ui.send("voice_state", status="thinking", transcript=text)
            self.speech.new_turn()
            self.timings["text"] = text
            await self.agent.handle(text, self.timings)
            await self.speech.wait_done()
            await self._after_turn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("turn failed")
            await self.speech.say("Sorry, something went wrong.")
            await self.speech.wait_done()
            await self._after_turn()

    async def _after_turn(self, empty=False):
        self.last_activity = time.monotonic()
        if not empty:
            self._log_latency()
        if self.timings.get("sleep_after"):
            await self._set(State.SLEEPING)
            return
        if self.timings.get("no_follow_up") or (empty and self.timings.get("trigger") == "follow_up"):
            await self._set(State.IDLE)
            return
        await asyncio.sleep(config.FOLLOW_UP_TAIL_MS / 1000)
        self.follow_deadline = time.monotonic() + config.FOLLOW_UP_MS / 1000
        self.onset_ms = 0
        self.vad.reset()
        await self._set(State.FOLLOW_UP, follow_up_ms_left=config.FOLLOW_UP_MS)

    def _log_latency(self):
        t = self.timings
        base = t.get("t_end_speech")
        if base is None:
            return

        def ms(k):
            return round((t[k] - base) * 1000) if k in t else ""

        row = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "trigger": t.get("trigger"),
               "route": t.get("route", ""), "stt_ms": ms("t_stt"), "first_token_ms": ms("first_token"),
               "first_audio_ms": ms("t_first_audio"), "first_action_ms": ms("first_action"), "text": t.get("text", "")}
        new = not self.latency_csv.exists()
        with self.latency_csv.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
        log.info("latency %s", {k: v for k, v in row.items() if k.endswith("_ms")})
