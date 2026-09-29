"""Calibrates the voice-match threshold with different Kokoro voices standing in for different people:
enroll one voice with 3 sentences, then score new sentences from the same voice vs other voices."""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.speaker import VoicePrint  # noqa: E402
from jarvis.tts.kokoro_tts import KokoroTTS  # noqa: E402

ENROLL = ["The quick brown fox jumps over the lazy dog.", "Scar, please open my documents folder.",
          "I usually study late in the evening after college."]
TEST = ["What's the weather going to be like tomorrow morning?", "Close this window and open YouTube for me.",
        "Remind me to call my mother in twenty minutes."]
VOICES = ["am_michael", "am_adam", "bm_george", "af_heart", "af_bella", "bf_emma", "am_puck", "bm_lewis"]

tts = KokoroTTS()
vp = VoicePrint()


def clip(text, voice):
    tts.voice = voice
    a, sr = tts.synth(text)
    return np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)


same, diff = [], []
t_embed = []
for enrolled in VOICES[:4]:
    vp.samples = []
    for s in ENROLL:
        vp.add_sample(clip(s, enrolled))
    ref = np.mean(vp.samples, axis=0)
    ref /= np.linalg.norm(ref)
    vp.samples = []
    for v in VOICES:
        for s in TEST:
            c = clip(s, v)
            t = time.perf_counter()
            e = vp.embed(c)
            t_embed.append((time.perf_counter() - t) * 1000)
            sim = float(np.dot(ref, e))
            (same if v == enrolled else diff).append(sim)
print(f"same speaker : min {min(same):.2f}  mean {np.mean(same):.2f}")
print(f"other speaker: max {max(diff):.2f}  mean {np.mean(diff):.2f}  95th pct {np.percentile(diff, 95):.2f}")
print(f"embedding time: {np.median(t_embed):.0f} ms")
