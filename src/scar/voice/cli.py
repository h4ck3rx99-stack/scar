"""`scar --voice` and `scar voice test`."""

from __future__ import annotations

import asyncio
import time

import numpy as np

from scar.cli.render import Renderer, console
from scar.config.settings import Settings


async def run_voice(settings: Settings, renderer: Renderer) -> int:
    from scar.cli.repl import Repl
    from scar.cli.session import EmbeddedSession
    from scar.voice.ptt import PushToTalk
    from scar.voice.session import VoiceSession

    session = EmbeddedSession(settings, renderer)
    if not await session.start():
        console.print("[red]Another SCAR runtime is running; stop it (`scar daemon stop`) to use voice here.[/red]")
        return 1
    svc = session.rt.services
    mode = "wake" if settings.wake_word and settings.voice_enabled else "ptt"
    voice = VoiceSession(svc, mode=mode, on_text=lambda t: console.print(f"[magenta]{t}[/magenta]"))
    ptt = PushToTalk(settings.ptt_hotkey, voice.push_to_talk)
    try:
        await voice.start()
        if ptt.start():
            how = (f"say '{settings.wake_word.replace('_', ' ')}' or press {settings.ptt_hotkey}" if mode == "wake"
                   else f"press {settings.ptt_hotkey}")
            console.print(f"[bold]Voice ready[/bold]: {how}, then speak. You can also type below.")
        else:
            console.print(f"[yellow]{ptt.error}[/yellow]")
    except Exception as exc:  # noqa: BLE001 - audio init failure: text-only fallback
        console.print(f"[yellow]Voice unavailable ({exc}); continuing with text.[/yellow]")
    try:
        await Repl(settings, session, renderer).loop()
    finally:
        ptt.stop()
        await voice.stop()
        await session.stop()
    return 0


async def voice_selftest(settings: Settings, seconds: float) -> int:
    """Record, report the level, transcribe, and speak the transcript back."""
    from scar.runtime.builder import build_services
    from scar.voice.audio_io import Microphone, Player, resolve_device, rms_level

    svc = build_services(settings)
    try:
        mic = Microphone(resolve_device(settings.mic_device, "input"))
        mic.start()
        console.print(f"Recording {seconds:.0f}s — say something…")
        frames: list[np.ndarray] = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            f = await mic.read(0.5)
            if f is not None:
                frames.append(f)
        mic.stop()
        if not frames:
            console.print("[red]No audio captured.[/red]")
            return 1
        pcm = np.concatenate(frames)
        console.print(f"Level: {rms_level(pcm):.4f} RMS ({len(pcm) / 16000:.1f}s)")
        t0 = time.monotonic()
        result = await svc.stt.transcribe(pcm.astype(np.int16).tobytes(), 16000)
        console.print(f"Heard ({result.provider}, {time.monotonic() - t0:.1f}s): {result.text!r}")
        audio = await svc.tts.synthesize(f"You said: {result.text or 'nothing I could understand'}")
        player = Player(resolve_device(settings.speaker_device, "output"))
        if audio.encoded:
            await asyncio.to_thread(player.play_encoded_sync, audio.encoded, audio.encoding or "mp3")
        else:
            await asyncio.to_thread(player.play_pcm_sync, audio.pcm16, audio.sample_rate)
        console.print(f"Spoke with {audio.provider}.")
        return 0
    finally:
        await svc.stt.aclose()
        svc.db.close()
