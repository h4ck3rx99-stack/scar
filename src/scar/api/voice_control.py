"""Voice control from the app: start/stop the in-runtime voice session, push-to-talk, privacy mute, stop speaking."""

from __future__ import annotations

from typing import Any


async def voice_command(services: Any, action: str, mode: str | None) -> dict[str, Any]:
    from scar.voice.session import VoiceSession

    v = services.voice
    if action == "start":
        if v is not None and v.active:
            return {"ok": True, "voice": v.state_dict()}
        chosen = mode or ("wake" if services.settings.voice_enabled and services.settings.wake_word else "ptt")
        v = VoiceSession(services, mode=chosen)
        try:
            await v.start()
        except Exception as exc:  # noqa: BLE001 - device/driver errors become a readable message in the app
            services.voice = None
            return {"ok": False, "message": f"Couldn't start the microphone: {exc}. Check Settings → Voice.",
                    "voice": {"state": "off", "mic_active": False, "muted": False, "mode": chosen}}
        return {"ok": True, "voice": v.state_dict()}
    if v is None or not v.active:
        return {"ok": False, "message": "Voice is off. Turn it on first.",
                "voice": {"state": "off", "mic_active": False, "muted": False, "mode": ""}}
    if action == "stop":
        await v.stop()
        return {"ok": True, "voice": {"state": "off", "mic_active": False, "muted": False, "mode": v.mode}}
    if action == "push_to_talk":
        if v.user_muted:
            return {"ok": False, "message": "The microphone is muted.", "voice": v.state_dict()}
        v.push_to_talk()
    elif action == "mute":
        v.set_muted(True)
    elif action == "unmute":
        v.set_muted(False)
    elif action == "stop_speaking":
        v.stop_speaking()
    return {"ok": True, "voice": v.state_dict()}


def voice_devices() -> dict[str, Any]:
    """Microphones and speakers, deduplicated by name (Windows lists each device once per audio API)."""
    from scar.voice.audio_io import list_devices

    mics: dict[str, dict[str, Any]] = {}
    speakers: dict[str, dict[str, Any]] = {}
    for d in list_devices():
        if d["hostapi"] not in ("MME", "Windows WASAPI"):
            continue
        target = mics if d["inputs"] > 0 else speakers if d["outputs"] > 0 else None
        if target is None:
            continue
        name = str(d["name"]).strip()
        if name.lower().startswith(("microsoft sound mapper", "primary sound")):
            continue
        entry = target.setdefault(name, {"name": name, "default": False})
        entry["default"] = entry["default"] or bool(d["default"])

    def merged(found: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        # MME cuts names at 31 characters: fold "Microphone (HyperX Cloud Stinge" into the full WASAPI name
        import re

        def ascii_(s: str) -> str:  # MME and WASAPI encode characters like "®" differently
            return re.sub(r"[^\x20-\x7e]", "", s).strip()

        names = sorted(found, key=len, reverse=True)
        out: dict[str, dict[str, Any]] = {}
        for n in names:
            full = next((o for o in out if len(ascii_(n)) >= 26 and ascii_(o).startswith(ascii_(n))), None)
            if full is not None:
                out[full]["default"] = out[full]["default"] or found[n]["default"]
            else:
                out[n] = dict(found[n])
        return sorted(out.values(), key=lambda x: (not x["default"], x["name"]))

    return {"microphones": merged(mics), "speakers": merged(speakers)}


async def voice_test(services: Any, seconds: float = 4.0, speak: bool = True) -> dict[str, Any]:
    """Record a few seconds from the configured microphone, transcribe, and (optionally) say it back."""
    import asyncio
    import time

    import numpy as np

    from scar.voice.audio_io import Microphone, Player, resolve_device, rms_level

    if services.voice is not None and services.voice.active:
        return {"ok": False, "message": "Turn voice off before testing the microphone."}
    try:
        mic = Microphone(resolve_device(services.settings.mic_device, "input"))
        mic.start()
    except Exception as exc:  # noqa: BLE001 - device errors vary; report them plainly
        return {"ok": False, "message": f"Couldn't open the microphone: {exc}"}
    frames: list[Any] = []
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            f = await mic.read(0.5)
            if f is not None:
                frames.append(f)
    finally:
        mic.stop()
    if not frames:
        return {"ok": False, "message": "No sound came from the microphone. Check that it isn't muted in Windows."}
    pcm = np.concatenate(frames)
    level = float(rms_level(pcm))
    t0 = time.monotonic()
    result = await services.stt.transcribe(pcm.astype(np.int16).tobytes(), 16000)
    out: dict[str, Any] = {"ok": True, "level": round(level, 4), "heard": result.text, "stt": result.provider,
                           "stt_seconds": round(time.monotonic() - t0, 2)}
    if level < 0.002:
        out["hint"] = "The recording was almost silent; the microphone may be muted or the wrong one."
    if speak:
        try:
            audio = await services.tts.synthesize(f"You said: {result.text or 'nothing I could understand'}")
            player = Player(resolve_device(services.settings.speaker_device, "output"))
            if audio.encoded:
                await asyncio.to_thread(player.play_encoded_sync, audio.encoded, audio.encoding or "mp3")
            else:
                await asyncio.to_thread(player.play_pcm_sync, audio.pcm16, audio.sample_rate)
            out["tts"] = audio.provider
        except Exception as exc:  # noqa: BLE001 - speech output problems are reported, transcription still stands
            out["tts_error"] = str(exc)[:200]
    return out
