"""Scar's voice and speaking speed, changeable by voice and remembered across restarts."""
from jarvis import config, runtime
from jarvis.skills.store import Store

_store = Store("voice", {})

# Friendly descriptions -> Kokoro voice ids.
VOICES = {
    "default": "am_michael", "male": "am_michael", "man": "am_michael", "deep": "am_fenrir", "deeper": "am_fenrir",
    "female": "af_heart", "woman": "af_heart", "girl": "af_bella", "soft": "af_nicole", "calm": "af_sarah",
    "british": "bm_george", "british male": "bm_george", "british man": "bm_george", "british female": "bf_emma",
    "british woman": "bf_emma", "american male": "am_adam", "american female": "af_heart", "young": "am_puck",
    "jarvis": "bm_lewis", "butler": "bm_lewis", "posh": "bm_lewis", "friendly": "af_heart", "robot": "am_echo",
}


def apply_saved():
    s = _store.load()
    if runtime.tts is not None:
        runtime.tts.voice = s.get("voice", config.KOKORO_VOICE)
        from jarvis.core import settings
        runtime.tts.speed = settings.get("voice_speed") if "voice_speed" in settings._store.load() else s.get("speed", config.KOKORO_SPEED)


def set_voice(description: str):
    key = description.lower().replace(" voice", "").replace("a ", "").strip()
    voice = VOICES.get(key)
    if voice is None:
        from rapidfuzz import process
        m = process.extractOne(key, VOICES.keys(), score_cutoff=70)
        voice = VOICES[m[0]] if m else None
    if voice is None:
        return None
    s = _store.load()
    s["voice"] = voice
    _store.save(s)
    if runtime.tts is not None:
        runtime.tts.voice = voice
    return voice


def change_speed(direction: str):
    s = _store.load()
    speed = s.get("speed", config.KOKORO_SPEED)
    speed = {"faster": speed + 0.15, "slower": speed - 0.15, "normal": config.KOKORO_SPEED}.get(direction, speed)
    speed = round(max(0.7, min(1.6, speed)), 2)
    s["speed"] = speed
    _store.save(s)
    from jarvis.core import settings
    settings.set_value("voice_speed", speed)   # one source of truth for "what are your settings"
    return speed
