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

from rapidfuzz import fuzz

from jarvis import config, runtime

log = logging.getLogger("session")
BLOCK_MS = config.BLOCK * 1000 // config.SAMPLE_RATE  # 32

# Whisper's classic hallucinations on noise/silence.
_JUNK = {"", "you", "thank you", "thanks", "bye", "thank you for watching", "thanks for watching", "okay", "hmm", "uh", "um", "so", "the"}
_WAKE_PREFIX = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|a|yo)\W+)?(?:jarvis|jervis|jarvi|jarves|travis|vis|scar|skar|scarr|scaar)\b\W*", re.I)
_GREETING = {"hey", "hi", "ok", "okay", "yo", "hello", "a"}
_NOT_NAME = {"oscar", "scare", "scared", "scary", "scarf", "scars", "scarce"}
ENROLL_LINES = [
    "Please say: Scar, open my documents and check the weather for today.",
    "Now say: The quick brown fox jumps over the lazy dog near the river bank.",
    "Last one: I usually study in the evening and listen to music while I work.",
]
# Said over Scar's voice, these cut it off.
# A sentence that stops on one of these words was cut off by a pause ("open Chrome and search about ...").
_DANGLING = re.compile(r"(?:\b(?:search|tell me|talk|know|information|info|learn|read) about|\b(?:and|or|the|an|my|your|called|named|"
                       r"but|because|into|than|search for|open|play|type|tell me|show me|search|put|some|a)\b)\W*$", re.I)
_INTERRUPT = re.compile(r"^\W*(?:ok(?:ay)?\W+)?(?:stop|wait|shut up|enough|quiet|be quiet|cancel|hold on|never ?mind|pause|that's enough)\b", re.I)


def name_call(text: str):
    """If the utterance starts with the assistant's name ("Scar", "Hey Scar, ..."), return the rest of the
    sentence ("" if nothing followed); otherwise None."""
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    if words and words[0] in _GREETING:
        words = words[1:]
    if not words:
        return None
    first, name = words[0], config.NAME.lower()
    # Whisper's usual spellings of "Scar" (from real logs: "Scott.").
    spellings = {name, "skar", "scarr", "scaar", "sccar", "skarr", "scott", "scot", "skaar"} if name == "scar" else {name}
    if first in _NOT_NAME or (first not in spellings and fuzz.ratio(first, name) < 86):
        return None
    return " ".join(text.split()[len(text.split()) - len(words) + 1:]).strip(" ,.!?")


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"
    SLEEPING = "sleeping"
    STANDBY = "standby"     # fully hidden and silent until called by name / wake word


def strip_wake(text: str) -> str:
    return _WAKE_PREFIX.sub("", text.strip()).strip()


class Session:
    def __init__(self, ui, mic, vad, wake, stt, speech, agent_factory):
        self.ui, self.mic, self.vad, self.wake, self.stt, self.speech = ui, mic, vad, wake, stt, speech
        self.agent = agent_factory(self._control)
        self.follow_up_chain = 0      # consecutive follow-up turns without a wake word
        self.voiced_energy = []       # per-block energy of the current utterance's speech
        self.user_level = None        # typical loudness of the user's own voice (learned from name/click turns)
        self.aec = self.aec_ref = None  # echo canceller + what the speaker played (set up by __main__)
        self.aec_saved = time.monotonic()
        self.enrolling = 0            # enrollment lines still to be read ("learn my voice")
        from jarvis.core import settings
        self.always_listen = bool(settings.get("always_listen"))   # act on requests without the name (not in standby)
        settings.on_change("always_listen", lambda v: setattr(self, "always_listen", bool(v)))
        settings.on_change("only_my_voice", self._set_voice_filter)
        self.pending_prefix = None    # first half of a sentence cut off by a pause
        self.last_audio = time.monotonic()
        self.back_task = None         # "do not disturb for 30 minutes": comes back on its own
        self.mic_on = True            # privacy switch: off = ignore the mic except click / hotkey turns
        self.dictating = False        # every utterance is typed out until "stop dictation"
        vp = runtime.voiceprint
        self.voice_filter = bool(vp is not None and vp.enrolled and settings.get("only_my_voice"))
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
        # Name spotting while idle: short speech segments are transcribed and checked for "Scar ...".
        self.spot_blocks = []
        self.spot_speech_ms = self.spot_silence_ms = 0
        self.spot_task = None

    def _set_voice_filter(self, on):
        vp = runtime.voiceprint
        self.voice_filter = bool(on and vp is not None and vp.enrolled)

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
        was_sleeping = self.state in (State.SLEEPING, State.STANDBY)
        self.utter = list(self.preroll)[-preroll_blocks:] if preroll_blocks else []
        self.listen_started = time.monotonic()
        self.speech_ms = self.silence_ms = 0
        self.voiced_energy = []
        # "Hey Jarvis, minimize Chrome" in one breath: the user is usually still talking when the wake fires.
        self.speech_started = reason in ("follow_up",)
        self.last_activity = time.monotonic()
        self.timings = {"trigger": reason, "t_listen": time.perf_counter()}
        if was_sleeping or reason == "boot":
            await self.ui.send("summon", entry=random.randint(0, 3), reason=reason)
        await self._set(State.LISTENING)

    async def _control(self, name, args=None):
        say = self.speech.say
        vp = runtime.voiceprint
        if name == "stop":
            self.speech.cancel()
            self.timings["no_follow_up"] = True
        elif name == "standby":
            minutes = (args or {}).get("minutes")
            if self.back_task and not self.back_task.done():
                self.back_task.cancel()
            if minutes:
                n = f"{minutes:g} minute{'s' * (minutes != 1)}" if minutes < 60 else f"{minutes / 60:g} hour{'s' * (minutes != 60)}"
                await say(f"Okay, I'll stay quiet for {n}. Say {config.NAME} if you need me sooner.")
                self.back_task = asyncio.get_running_loop().create_task(self._back_after(minutes * 60))
            else:
                await say(f"Going on standby. Just say {config.NAME} when you need me.")
            self.timings["standby_after"] = True
        elif name == "go_to_sleep":
            await say(random.choice(["Going to sleep.", "Call me if you need me.", "I'll be around."]))
            self.timings["sleep_after"] = True
        elif name == "learn_voice":
            if vp is None:
                await say("Voice recognition isn't available on this setup.")
                return
            vp.samples = []
            self.enrolling = len(ENROLL_LINES)
            await say(f"Let's learn your voice. After each line, read it back to me. {ENROLL_LINES[0]}")
            self.timings["keep_listening"] = True
        elif name == "forget_voice":
            if vp is not None:
                vp.forget()
            await say("Done. I'll listen to anyone again.")
        elif name in ("only_me", "everyone"):
            if name == "only_me" and (vp is None or not vp.enrolled):
                await say("I need to learn your voice first. Say: learn my voice.")
                return
            self.voice_filter = name == "only_me"
            await say("Okay, only you from now on." if self.voice_filter else "Okay, I'll listen to everyone.")
        elif name == "dictation":
            self.dictating = True
            await say("Dictation on. I'll type what you say. Say stop dictation when you're done.")
            self.timings["keep_listening"] = True
        elif name in ("suggestions_on", "suggestions_off"):
            from jarvis.core import settings
            settings.set_value("suggestions", name == "suggestions_on")
            await say("Suggestions back on." if name == "suggestions_on" else "Okay, no more suggestions unless you ask.")
        elif name in ("listen_always", "listen_on_call"):
            from jarvis.core import settings
            settings.set_value("always_listen", name == "listen_always")
            await say("Okay, I'm always listening. No need to say my name." if self.always_listen
                      else f"Okay, I'll only respond when you say {config.NAME}.")
        elif name in ("mic_off", "mic_on"):
            self.mic_on = name == "mic_on"
            await self.ui.send("say", text="🎙 Mic on" if self.mic_on else "🔇 Mic off: click me or press Ctrl+Shift+Space to talk")
            await say("Mic's on." if self.mic_on else "Mic off. Click me or press Control Shift Space when you need me.")
        elif name == "place":
            spot = (args or {}).get("spot", "here")
            await self.ui.send("place", spot=spot, park=spot != "roam")
            await say(random.choice(["On my way.", "Sure.", "Moving."]) if spot not in ("here", "roam") else
                      ("I'll stay right here." if spot == "here" else "Free to roam!"))
        elif name in ("phone_on", "phone_off"):
            phone = runtime.phone
            if phone is None:
                await say("Phone control isn't available.")
            elif name == "phone_off":
                await phone.stop()
                await say("Phone control is off.")
            else:
                url, pin = await phone.start()
                await self.ui.send("say", text=f"📱 Open {url} on your phone · PIN {pin}")
                await say(f"Phone control is on. Open the address on my bubble on your phone, same Wi-Fi, and enter the PIN {' '.join(pin)}.")

    async def _back_after(self, seconds):
        await asyncio.sleep(seconds)
        if self.state == State.STANDBY:
            log.info("do-not-disturb time is up")
            await self.ui.send("summon", entry=random.randint(0, 3), reason="wake")
            self.last_activity = time.monotonic()
            await self._set(State.IDLE)
            await self.speech.say(random.choice(["I'm back!", "Quiet time's over. I'm here if you need me.", "And I'm back."]))

    async def announce(self, text, kind="reminder"):
        """Speak unprompted (timer done, reminder due). Opens the follow-up window afterwards."""
        await self.ui.send("alert", text=text, kind=kind)
        if self.state == State.STANDBY:
            # Standby means "stay out of sight": say it, but don't come out.
            await self.speech.say(text)
            return
        if self.state == State.SLEEPING:
            await self.ui.send("summon", entry=random.randint(0, 3), reason="alert")
        if self.state in (State.LISTENING, State.THINKING, State.SPEAKING):
            await self.speech.say(text)
            return
        self.timings = {"trigger": "alert"}
        self.speech.new_turn()
        await self.speech.say(text)
        await self._set(State.SPEAKING)
        await self.speech.wait_done()
        await self._after_turn()

    # --------------------------------------------------------------- audio loop
    async def run(self):
        last = time.monotonic()
        while True:
            block = await self.mic.queue.get()
            now = time.monotonic()
            if now - last > 5:
                # The PC slept (or the mic stalled): whatever we were doing is stale. Start fresh and quiet.
                log.info("audio resumed after %.0f s gap; resetting to idle", now - last)
                if self.turn_task and not self.turn_task.done():
                    self.turn_task.cancel()
                self.speech.cancel()
                self._reset_spot()
                self.follow_up_chain = 0
                self.last_activity = now
                await self._set(State.IDLE)
            last = now
            self.last_audio = now
            try:
                await self._process(block)
            except Exception:  # noqa: BLE001 - never let one bad block kill the ears
                log.exception("audio block failed")

    async def _process(self, block):
        now = time.monotonic()
        if not self.mic_on and not (self.state == State.LISTENING and self.timings.get("trigger") in ("click", "continue", "rest")):
            return   # mic switched off: only an explicit click / hotkey turn is heard
        if self.aec is not None:
            # Remove Scar's own voice (what the speaker is playing) before anything listens to the mic.
            block = self.aec.process(block, self.aec_ref.take(len(block)))
            if now - self.aec_saved > 60:
                self.aec_saved = now
                self.aec.save(config.APPDATA_DIR / "aec_filter.npz")
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
        if st in (State.IDLE, State.SLEEPING, State.STANDBY):
            if woke:
                self._reset_spot()
                await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))
            else:
                if config.NAME_WAKE and self.stt is not None:
                    self._spot(block, prob)
                if st == State.IDLE and now - self.last_activity > config.SLEEP_AFTER_S:
                    await self._set(State.SLEEPING)

        elif st == State.LISTENING:
            self.utter.append(block)
            if prob >= config.VAD_ON:
                self.voiced_energy.append(float(np.mean(block ** 2)))
                self.speech_ms += BLOCK_MS
                self.silence_ms = 0
                if self.speech_ms >= config.SPEECH_ONSET_MS:
                    self.speech_started = True
            elif prob < config.VAD_OFF and self.speech_started:
                self.silence_ms += BLOCK_MS
            elapsed_ms = (now - self.listen_started) * 1000
            if self.speech_started and self.silence_ms >= config.END_SILENCE_MS:
                await self._finish_utterance()
            elif elapsed_ms > (config.MAX_FOLLOW_UP_UTTERANCE_MS if self.timings.get("trigger") == "follow_up" else config.MAX_UTTERANCE_MS):
                if self.timings.get("trigger") == "follow_up":
                    log.info("follow-up speech ran too long: treating it as background talk")
                    self.last_activity = now
                    await self._set(State.IDLE)
                else:
                    await self._finish_utterance()
            elif not self.speech_started and elapsed_ms > (20000 if self.timings.get("trigger") == "continue" else config.NO_SPEECH_TIMEOUT_MS):
                log.info("no speech after trigger")
                self.last_activity = now
                if self.pending_prefix:
                    # The rest of the sentence never came: do what we can with the first half.
                    text, self.pending_prefix = self.pending_prefix, None
                    self.timings = {"trigger": "wake", "t_end_speech": time.perf_counter(), "unfinished_ok": True}
                    await self._set(State.THINKING)
                    self.turn_task = asyncio.get_running_loop().create_task(self._turn(text=text))
                    return
                if self.dictating or self.enrolling:
                    self.dictating, self.enrolling = False, 0
                    await self.speech.say("Stopping there.")
                await self._set(State.IDLE)

        elif st == State.FOLLOW_UP:
            if woke:
                await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))
                return
            self.onset_ms = self.onset_ms + BLOCK_MS if prob >= config.VAD_ON_FOLLOW_UP else 0
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

        elif st in (State.THINKING, State.SPEAKING):
            if woke:
                log.info("barge-in")
                await self.start_listening("wake", preroll_blocks=int(0.8 * 1000 / BLOCK_MS))
            elif config.NAME_WAKE and self.stt is not None:
                # Listen for "Scar ..." / "stop" over our own voice; short segments keep it snappy.
                self._spot(block, prob, max_ms=3500)

    # ------------------------------------------------------------ name spotting
    def _reset_spot(self):
        self.spot_blocks = []
        self.spot_speech_ms = self.spot_silence_ms = 0

    def _spot(self, block, prob, max_ms=7000):
        """Collect short speech bursts; when one ends, transcribe it and check whether it starts with the name."""
        if prob >= config.VAD_ON:
            if not self.spot_blocks:
                self.spot_blocks = list(self.preroll)[-10:]  # ~0.3 s before onset
            self.spot_speech_ms += BLOCK_MS
            self.spot_silence_ms = 0
        elif self.spot_blocks:
            self.spot_silence_ms += BLOCK_MS
        if not self.spot_blocks:
            return
        self.spot_blocks.append(block)
        dur_ms = len(self.spot_blocks) * BLOCK_MS
        if self.spot_silence_ms >= 400 or dur_ms >= max_ms:
            blocks, speech_ms = self.spot_blocks, self.spot_speech_ms
            self._reset_spot()
            if speech_ms < 200 or (self.spot_task and not self.spot_task.done()):
                return
            audio = np.concatenate(blocks)
            self.spot_task = asyncio.get_running_loop().create_task(self._check_name(audio))

    async def _maybe_ambient_command(self, text, audio, t_end):
        """Always-listening mode: act on speech that wasn't prefixed with the name, but only if it's clearly
        addressed to Scar (yes/no LLM check), loud enough to be the user, and the user's voice if enrolled."""
        words = text.split()
        norm = re.sub(r"[^\w\s']", "", text.lower()).strip()
        if len(words) < 2 or len(words) > 25 or norm in _JUNK:
            return
        from jarvis.brain.router import route_all
        hits = route_all(text)
        if hits and hits[0][0] in ("smalltalk", "confirm", "stop"):
            return                      # "thanks", "yes", "stop" said to someone else
        if not hits and len(words) < 3:
            return                      # "show me", "go ahead": too vague without the name
        loop = asyncio.get_running_loop()
        if self.user_level:
            blocks = audio[: len(audio) // config.BLOCK * config.BLOCK].reshape(-1, config.BLOCK)
            level = float(np.sqrt(np.percentile(np.mean(blocks ** 2, axis=1), 75))) if len(blocks) else 0.0
            if level < self.user_level * config.FOLLOW_UP_MIN_LEVEL:
                return
        vp = runtime.voiceprint
        if vp is not None:
            if await loop.run_in_executor(None, vp.is_self, audio):
                return
            if self.voice_filter and vp.enrolled and await loop.run_in_executor(None, vp.is_user, audio) is False:
                return
        if not await self.agent._is_directed(text):
            return
        if self.state not in (State.IDLE, State.SLEEPING):   # something else started meanwhile
            return
        log.info("always-listening: acting on %r", text)
        if self.state == State.SLEEPING:
            await self.ui.send("summon", entry=random.randint(0, 3), reason="wake")
        self.timings = {"trigger": "ambient", "t_end_speech": t_end, "t_stt": time.perf_counter()}
        await self._set(State.THINKING)
        self.turn_task = loop.create_task(self._turn(text=text))

    async def _check_name(self, audio):
        t_end = time.perf_counter()
        text = await asyncio.get_running_loop().run_in_executor(None, self.stt.transcribe, audio)
        busy = self.state in (State.THINKING, State.SPEAKING)
        if not busy and self.state not in (State.IDLE, State.SLEEPING, State.STANDBY):
            return
        if busy and _INTERRUPT.match(text.strip()):
            # "Stop" / "wait" / "shut up" while it's talking.
            log.info("interrupted: %r", text)
            if self.turn_task and not self.turn_task.done():
                self.turn_task.cancel()
            self.speech.cancel()
            self.last_activity = time.monotonic()
            await self._set(State.IDLE)
            return
        rest = name_call(text)
        if rest is None:
            if not busy and self.state != State.STANDBY and self.always_listen:
                await self._maybe_ambient_command(text, audio, t_end)
            return
        log.info("name call%s: %r", " (barge-in)" if busy else "", text)
        vp = runtime.voiceprint
        if not busy and self.voice_filter and vp is not None and vp.enrolled:
            if await asyncio.get_running_loop().run_in_executor(None, vp.is_user, audio) is False:
                log.info("name call ignored: not the enrolled user's voice")
                return
        if not busy:  # (during barge-in the mic also hears Scar's own voice, so don't learn from it)
            blocks = audio[: len(audio) // config.BLOCK * config.BLOCK].reshape(-1, config.BLOCK)
            energy = np.mean(blocks ** 2, axis=1)
            level = float(np.sqrt(np.percentile(energy, 75))) if len(energy) else 0.0
            if level > 0:
                self.user_level = level if self.user_level is None else 0.7 * self.user_level + 0.3 * level
        if busy:
            if self.turn_task and not self.turn_task.done():
                self.turn_task.cancel()
            self.speech.cancel()
            if _INTERRUPT.match(rest):
                self.last_activity = time.monotonic()
                await self._set(State.IDLE)
                return
        from jarvis.brain.router import route_all
        if len(rest.split()) >= 2 or (rest and route_all(rest)):
            # "Scar, open YouTube" / "Scar, standby" in one breath: run it straight away.
            if self.state in (State.SLEEPING, State.STANDBY):
                await self.ui.send("summon", entry=random.randint(0, 3), reason="wake")
            self.timings = {"trigger": "wake", "t_end_speech": t_end, "t_stt": time.perf_counter()}
            await self._set(State.THINKING)
            self.turn_task = asyncio.get_running_loop().create_task(self._turn(text=rest))
        else:
            await self.start_listening("wake")

    async def _finish_utterance(self):
        audio = np.concatenate(self.utter) if self.utter else np.zeros(0, np.float32)
        self.utter = []
        self.timings["t_end_speech"] = time.perf_counter()
        # Loudness of the voiced part (median, so one cough or door slam doesn't skew it).
        level = float(np.sqrt(np.median(self.voiced_energy))) if self.voiced_energy else 0.0
        trigger = self.timings.get("trigger")
        if trigger in ("wake", "click") and level > 0:
            # Learn how loud the user's own voice is at the mic (slow moving average).
            self.user_level = level if self.user_level is None else 0.7 * self.user_level + 0.3 * level
        elif trigger == "follow_up" and self.user_level and level < self.user_level * config.FOLLOW_UP_MIN_LEVEL:
            log.info("follow-up speech too quiet (%.4f vs your %.4f): someone else / background", level, self.user_level)
            self.last_activity = time.monotonic()
            self.follow_up_chain = 0
            await self._set(State.IDLE)
            return
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
                vp, trigger = runtime.voiceprint, self.timings.get("trigger")
                if self.enrolling:
                    return await self._enroll_step(audio)
                if vp is not None and trigger in ("follow_up", "wake") and not self.dictating:
                    # Voice ID: Scar's own voice echoing back, or (if "only me" is on) someone else.
                    if trigger == "follow_up" and await loop.run_in_executor(None, vp.is_self, audio):
                        log.info("ignored: that was my own voice through the speakers")
                        self.last_activity = time.monotonic()
                        return await self._set(State.IDLE)
                    if self.voice_filter and vp.enrolled and await loop.run_in_executor(None, vp.is_user, audio) is False:
                        log.info("ignored: not the enrolled user's voice")
                        self.last_activity = time.monotonic()
                        return await self._set(State.IDLE)
                text = await loop.run_in_executor(None, self.stt.transcribe, audio)
                self.timings["t_stt"] = time.perf_counter()
            text = strip_wake(text)
            norm = re.sub(r"[^\w\s']", "", text.lower()).strip()
            log.info("heard: %r", text)
            if self.dictating:
                return await self._dictate(text)
            if norm in _JUNK:
                await self._after_turn(empty=True)
                return
            if self.pending_prefix:
                text, self.pending_prefix = f"{self.pending_prefix} {text}", None
                log.info("joined: %r", text)
            elif (audio is not None and _DANGLING.search(text) and len(text.split()) >= 2
                  and not self.timings.get("unfinished_ok")):
                log.info("sounds unfinished, listening for the rest: %r", text)
                self.pending_prefix = text
                await self.ui.send("voice_state", status="thinking", transcript=text + " …")
                self.turn_task = None           # don't let start_listening cancel this very task
                await self.start_listening("rest")
                return
            await self.ui.send("voice_state", status="thinking", transcript=text)
            self.speech.new_turn()
            self.timings["text"] = text
            follow_up = self.timings.get("trigger") == "follow_up"
            outcome = await self.agent.handle(text, self.timings, follow_up=follow_up)
            if outcome == "ignored":
                # Not meant for us: end the conversation silently instead of answering the room.
                self.last_activity = time.monotonic()
                self.follow_up_chain = 0
                await self._set(State.IDLE)
                return
            if outcome == "end":
                self.timings["no_follow_up"] = True   # "thanks" closes the conversation
            await self.speech.wait_done()
            await self._after_turn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("turn failed")
            await self.speech.say("Sorry, something went wrong.")
            await self.speech.wait_done()
            await self._after_turn()

    # ------------------------------------------------- voice enrollment / dictation
    async def _enroll_step(self, audio):
        vp = runtime.voiceprint
        ok = await asyncio.get_running_loop().run_in_executor(None, vp.add_sample, audio)
        if not ok:
            await self.speech.say("That was a bit short. Please read the whole line again.")
        else:
            self.enrolling -= 1
        done = len(ENROLL_LINES) - self.enrolling
        if self.enrolling > 0:
            await self.speech.say(("Got it. " if ok else "") + ENROLL_LINES[done])
            self.timings = {"trigger": "continue", "keep_listening": True}
        else:
            if vp.finish_enrollment():
                self.voice_filter = config.ONLY_MY_VOICE
                await self.speech.say("Perfect, I know your voice now. " +
                                      ("I'll only respond to you. Say listen to everyone to change that." if self.voice_filter else ""))
            else:
                await self.speech.say("Hmm, that didn't work. Let's try again later.")
            self.timings = {"trigger": "continue"}
        await self.speech.wait_done()
        await self._after_turn()

    async def _dictate(self, text):
        from jarvis.skills import screen
        if re.match(r"^\W*(?:stop|end|finish|exit) (?:dictation|dictating|typing)\b", text, re.I) or text.lower().strip(" .!") in ("stop", "stop dictation"):
            self.dictating = False
            await self.speech.say("Dictation off.")
            self.timings = {"trigger": "continue"}
            await self.speech.wait_done()
            return await self._after_turn()
        # Spoken punctuation, then type it where the cursor is.
        t = text.strip()
        for word, mark in (("full stop", "."), ("period", "."), ("comma", ","), ("question mark", "?"),
                           ("exclamation mark", "!"), ("new line", "\n"), ("next line", "\n"), ("new paragraph", "\n\n")):
            t = re.sub(rf"\s*\b{word}\b\.?", mark, t, flags=re.I)
        await asyncio.get_running_loop().run_in_executor(None, screen.type_text, t + " ", None)
        await self.ui.send("say", text=f"✍ {t}")
        self.timings = {"trigger": "continue", "keep_listening": True}
        await self._after_turn(empty=True)

    async def _after_turn(self, empty=False):
        self.last_activity = time.monotonic()
        if self.timings.get("keep_listening"):
            # Enrollment / dictation: straight back to listening, no wake word and no follow-up timer.
            return await self.start_listening("continue")
        self.follow_up_chain = self.follow_up_chain + 1 if self.timings.get("trigger") == "follow_up" else 0
        if self.follow_up_chain >= config.MAX_FOLLOW_UP_CHAIN:
            # A long run of wake-word-free turns: require the wake word again (Alexa does the same).
            self.follow_up_chain = 0
            await self._set(State.IDLE)
            return
        if not empty:
            self._log_latency()
        if self.timings.get("sleep_after"):
            await self._set(State.SLEEPING)
            return
        if self.timings.get("standby_after") or self.timings.get("return_to_standby"):
            await self._set(State.STANDBY)
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
