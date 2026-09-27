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
