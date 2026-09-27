"""Voice session (C10): microphone → VAD → wake word / push-to-talk → STT → agent → TTS.

Half-duplex with echo guarding: microphone frames are discarded while SCAR is speaking,
so SCAR can never hear (or approve) itself. Barge-in: the push-to-talk hotkey (or the
kill switch) stops speech immediately. "Stop"/"cancel" cancels the running task.
Approvals are handled through the same broker, inside a short listening window.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Any

import structlog

from scar.core.events import ApprovalRequested, Event, MicLevel, VoiceStateEvent
from scar.providers.errors import AllProvidersFailed
from scar.security.approval import ApprovalError, ApprovalResponse
from scar.security.grants import UserAuthority
from scar.voice.approval_voice import CONFIRM, is_revoke_command, is_stop_command, parse_approval
from scar.voice.audio_io import Microphone, Player, resolve_device
from scar.voice.vad import Endpointer, make_vad
from scar.voice.wakeword import WakeWord

log = structlog.get_logger("scar.voice")

MAX_SPOKEN_CHARS = 280


def speakable(text: str) -> str:
    """Only concise results are spoken; long content is left on screen."""
    t = re.sub(r"`+|\*+|#+", "", text).strip()
    t = re.sub(r"https?://\S+", "a link", t)
    if len(t) <= MAX_SPOKEN_CHARS and t.count("\n") <= 3:
        return t
    first = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    return (first[:200] + ". ") + "I've put the details on screen."


class VoiceSession:
    def __init__(self, services: Any, *, mode: str = "ptt", on_text: Any = None) -> None:
        self.s = services
        self.settings = services.settings
        self.mode = mode  # "ptt" | "wake" | "continuous"
        self.on_text = on_text  # callback(str) to show transcripts/replies on screen
        self.mic: Microphone | None = None
        self.player: Player | None = None
        self.vad = make_vad()
        self.wake: WakeWord | None = None
        self.active = False
        self._trigger = asyncio.Event()
        self._conversation_until = 0.0
        self._loop_task: asyncio.Task[None] | None = None
        self._speak_lock = asyncio.Lock()
        self.degraded_reason: str | None = None
        self.transcripts: list[str] = []
        self.phase = "idle"  # idle | listening | thinking | speaking (shown by the app)
        self.user_muted = False
        self._level_at = 0.0
        self._mic_device: int | None = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        mic_dev = resolve_device(self.settings.mic_device, "input")
        spk_dev = resolve_device(self.settings.speaker_device, "output")
        self.player = Player(spk_dev)
        self._mic_device = mic_dev
        self.mic = Microphone(mic_dev)
        self.mic.start()
        if self.mode == "wake":
            self.wake = WakeWord(self.settings.wake_word, self.settings.models_path / "openwakeword",
                                 self.settings.wake_word_threshold)
            await asyncio.to_thread(self.wake.load)
            self.s.local_models.register_component("wake-word", self.wake.unload, 20.0)
        self.active = True
        self.s.voice = self
        self.s.approvals.attach_channel("voice")
        self._loop_task = asyncio.create_task(self._loop(), name="scar-voice")
        self._approval_task = asyncio.create_task(self._approvals(), name="scar-voice-approvals")
        self._set_phase("idle")

    async def stop(self) -> None:
        self.active = False
        self.stop_speaking()
        for t in (self._loop_task, getattr(self, "_approval_task", None)):
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        if self.mic is not None:
            self.mic.stop()
        self.s.approvals.detach_channel("voice")
        if self.s.voice is self:
            self.s.voice = None
        self.s.bus.publish(VoiceStateEvent(state="off", mic_active=False, muted=False, mode=self.mode))

    # ------------------------------------------------------------------ state for the app
    @property
    def listening(self) -> bool:
        return self.active and self.phase == "listening"

    @property
    def speaking(self) -> bool:
        return self.active and self.phase == "speaking"

    def state_dict(self) -> dict[str, Any]:
        mic_on = self.mic is not None and self.mic.alive and not self.user_muted
        return {"state": self.phase if self.active else "off", "mic_active": mic_on, "muted": self.user_muted,
                "mode": self.mode, "degraded": self.degraded_reason or ""}

    def _set_phase(self, phase: str, transcript: str = "") -> None:
        self.phase = phase
        st = self.state_dict()
        self.s.bus.publish(VoiceStateEvent(state=st["state"], mic_active=st["mic_active"], muted=st["muted"],
                                           mode=self.mode, transcript=transcript))

    def set_muted(self, muted: bool) -> None:
        """Privacy mute: stops the capture stream itself (Windows' microphone indicator goes off), not just the
        processing."""
        self.user_muted = muted
        if self.mic is not None:
            if muted:
                self.mic.stop()
            elif not self.mic.alive:
                self.mic.start()
        self._set_phase(self.phase)

    def _publish_level(self, frame: Any) -> None:
        now = time.monotonic()
        if now - self._level_at < 0.1:
            return
        self._level_at = now
        from scar.voice.audio_io import rms_level

        self.s.bus.publish(MicLevel(level=min(1.0, float(rms_level(frame)) * 8)))

    async def _recover_mic(self) -> bool:
        """The microphone was unplugged or the default device changed: reopen the configured/default device."""
        for attempt in range(3):
            await asyncio.sleep(1.0 + attempt)
            try:
                if self.mic is not None:
                    self.mic.stop()
                self._mic_device = resolve_device(self.settings.mic_device, "input")
                self.mic = Microphone(self._mic_device)
                self.mic.start()
                self.degraded_reason = None
                self._show("Microphone reconnected.")
                self._set_phase("idle")
                return True
            except Exception as exc:  # noqa: BLE001 - PortAudio errors vary by device state
                self.degraded_reason = f"microphone unavailable: {exc}"
        return False

    def push_to_talk(self) -> None:
        """Hotkey callback (from a pynput thread): barge-in + start listening."""
        self.stop_speaking()
        loop = self._loop_ref
        loop.call_soon_threadsafe(self._trigger.set)

    @property
    def _loop_ref(self) -> asyncio.AbstractEventLoop:
        assert self._loop_task is not None
        return self._loop_task.get_loop()

    # ------------------------------------------------------------------ speaking
    def stop_speaking(self) -> None:
        if self.player is not None and self.player.playing.is_set():
            self.player.stop()

    async def speak(self, text: str) -> bool:
        text = speakable(text)
        if not text or self.player is None:
            return False
        async with self._speak_lock:
            self._set_phase("speaking")
            try:
                audio = await self.s.tts.synthesize(text)
            except AllProvidersFailed as exc:
                self.degraded_reason = f"speech output unavailable: {exc}"
                return False
            assert self.mic is not None
            self.mic.muted.set()  # half-duplex echo guard
            try:
                if audio.encoded:
                    done = await asyncio.to_thread(self.player.play_encoded_sync, audio.encoded, audio.encoding or "mp3")
                else:
                    done = await asyncio.to_thread(self.player.play_pcm_sync, audio.pcm16, audio.sample_rate)
            except Exception as exc:  # noqa: BLE001 - audio device errors: fall back to text
                self.degraded_reason = f"speaker error: {exc}"
                return False
            finally:
                await asyncio.sleep(0.25)  # let the room echo die down
                self.mic.drain()
                self.mic.muted.clear()
                self._set_phase("idle")
            return done

    # ------------------------------------------------------------------ listening
    async def listen_once(self, max_wait: float, max_seconds: float | None = None) -> str | None:
        """Capture one utterance (VAD endpointing) and transcribe it."""
        assert self.mic is not None
        ep = Endpointer(self.vad, max_seconds=max_seconds or self.settings.max_utterance_seconds)
        deadline = time.monotonic() + max_wait
        self._set_phase("listening")
        while self.active:
            if self.user_muted:
                self._set_phase("idle")
                return None
            frame = await self.mic.read(timeout=0.5)
            if frame is None:
                if not self.mic.alive:
                    raise RuntimeError("microphone disconnected")
                if not ep.started and time.monotonic() > deadline:
                    self._set_phase("idle")
                    return None
                continue
            self._publish_level(frame)
            pcm = ep.feed(frame)
            if pcm is not None:
                self._set_phase("thinking")
                result = await self.s.stt.transcribe(pcm, 16000)
                text = result.text.strip()
                if text:
                    self.transcripts.append(text)
                    del self.transcripts[:-50]
                    self._set_phase("thinking", transcript=text)
                else:
                    self._set_phase("idle")
                return text or None
            if not ep.started and time.monotonic() > deadline:
                return None
        return None

    async def _wait_trigger(self) -> None:
        if self.mode == "continuous" or time.monotonic() < self._conversation_until:
            return
        if self.mode == "ptt":
            await self._trigger.wait()
            self._trigger.clear()
            return
        assert self.mic is not None and self.wake is not None
        while self.active:
            if self._trigger.is_set():
                self._trigger.clear()
                return
            if self.user_muted:
                await asyncio.sleep(0.3)
                continue
            frame = await self.mic.read(timeout=0.5)
            if frame is None and not self.mic.alive:
                raise RuntimeError("microphone disconnected")
            if frame is not None and self.wake.feed(frame):
                self.s.local_models.component_used("wake-word")
                return

    async def _loop(self) -> None:
        while self.active:
            try:
                await self._wait_trigger()
                if self.mic is not None:
                    self.mic.drain()
                text = await self.listen_once(max_wait=6.0)
                if not text:
                    continue
                self._show(f"🎤 {text}")
                await self.handle_utterance(text)
                self._conversation_until = time.monotonic() + self.settings.voice_session_timeout
            except asyncio.CancelledError:
                raise
            except AllProvidersFailed as exc:
                self.degraded_reason = f"speech recognition unavailable: {exc}"
                self._show("Voice input is unavailable right now; please type instead.")
                await asyncio.sleep(5)
            except RuntimeError as exc:
                self.degraded_reason = str(exc)
                if "microphone" in str(exc) and not self.user_muted:
                    self._show("Microphone disconnected; trying to reconnect…")
                    if await self._recover_mic():
                        continue
                self._show(f"Voice problem: {exc}. Continuing with text.")
                self.active = False
                self._set_phase("idle")
                return
            except Exception as exc:
                log.exception("voice_loop_error")
                self._show(f"Voice error: {exc}")
                await asyncio.sleep(1)

    async def handle_utterance(self, text: str) -> None:
        tm = self.s.tasks
        if is_stop_command(text):
            self.stop_speaking()
            n = len(tm.cancel_all("voice stop")) if tm else 0
            await self.speak("Stopped." if n else "Okay.")
            return
        if is_revoke_command(text):
            await self.speak("Revoke all saved permissions? Say yes to confirm.")
            answer = await self.listen_once(self.settings.voice_approval_window, 4)
            if answer and CONFIRM.search(answer):
                n = self.s.grants.revoke_all(UserAuthority("voice", "voice revoke"))
                await self.speak(f"Revoked {n} permissions.")
            else:
                await self.speak("Kept them.")
            return
        if tm is None:
            return
        handle = await tm.submit(text, origin="voice")
        assert handle.future is not None
        self._set_phase("thinking", transcript=text)
        task = await handle.future
        self._show(task.result_summary)
        if not await self.speak(task.result_summary):
            self._set_phase("idle")

    # ------------------------------------------------------------------ approvals by voice
    async def _approvals(self) -> None:
        async with self.s.bus.subscribe({"approval_requested"}) as q:
            while self.active:
                ev: Event = await q.get()
                if isinstance(ev, ApprovalRequested) or ev.kind == "approval_requested":
                    await self._voice_approval(getattr(ev, "request_id", ""))

    async def _voice_approval(self, request_id: str) -> None:
        req = self.s.approvals.get(request_id)
        if req is None:
            return
        if req.critical:
            await self.speak("This needs your confirmation at the keyboard.")
            return
        await self.speak(f"{req.readback}. Allow?")
        heard = await self.listen_once(self.settings.voice_approval_window, 5)
        if self.s.approvals.get(request_id) is None:
            return  # resolved elsewhere (keyboard) meanwhile
        response = parse_approval(heard or "")
        if response is None:
            await self.speak("I didn't catch that; waiting for you at the keyboard.")
            return
        confirmed = False
        if response == ApprovalResponse.ALLOW_ALWAYS:
            await self.speak(f"Always allow: {req.readback}? Say yes to confirm.")
            again = await self.listen_once(self.settings.voice_approval_window, 4)
            confirmed = bool(again and CONFIRM.search(again))
            if not confirmed:
                response = ApprovalResponse.ALLOW_ONCE
        try:
            self.s.approvals.resolve(request_id, response, "voice", voice_readback_confirmed=confirmed)
        except ApprovalError as exc:
            await self.speak(f"I couldn't apply that: {exc}.")
            return
        await self.speak("Allowed." if response.value.startswith("allow") else "Denied.")

    def _show(self, text: str) -> None:
        if self.on_text is not None:
            self.on_text(text)
