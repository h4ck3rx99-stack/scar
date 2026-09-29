"""Echo canceller on ~40 s of Scar's real TTS speech through a simulated room: stable settings, per-5s ERLE."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.aec import BLOCK, EchoCanceller  # noqa: E402
from jarvis.tts.kokoro_tts import KokoroTTS  # noqa: E402

tts = KokoroTTS()
lines = ["The weather in Bengaluru is twenty five degrees with light clouds.", "Opening YouTube and searching for lofi music.",
         "Your battery is at sixty seven percent and charging.", "Tokyo is the capital of Japan, with about fourteen million people.",
         "I've saved that note for you. You have three items on your list.", "Timer set for five minutes. I'll let you know.",
         "Here is a quick summary of the article you were reading about solar panels and their efficiency.",
         "Minimizing Chrome. Closing Notepad. Moving Spotify to the left half of the screen."]
parts = []
for _ in range(3):
    for s in lines:
        a, sr = tts.synth(s)
        parts += [a, np.zeros(int(sr * 0.6), np.float32)]
ref24 = np.concatenate(parts)
ref = np.interp(np.arange(0, len(ref24), 1.5), np.arange(len(ref24)), ref24).astype(np.float32)
rng = np.random.default_rng(0)
rir = rng.standard_normal(3200) * np.exp(-np.arange(3200) / 900.0)
rir[0] += 3.0
rir = np.concatenate([np.zeros(int(0.18 * 16000)), rir]) * 0.08
mic = (np.convolve(ref, rir)[: len(ref)] + 0.001 * rng.standard_normal(len(ref))).astype(np.float32)

for mu, norm in ((0.5, 16), (0.3, 8), (0.15, 4)):
    aec = EchoCanceller(mu=mu)
    aec.norm = norm
    lin = np.concatenate([(aec.process(mic[i:i + BLOCK], ref[i:i + BLOCK]), aec.last_linear)[1]
                          for i in range(0, len(mic) - BLOCK, BLOCK)])
    w = 16000 * 5
    per = [10 * np.log10(np.mean(mic[s:s + w] ** 2) / (np.mean(lin[s:s + w] ** 2) + 1e-12)) for s in range(0, len(lin) - w, w)]
    print(f"mu={mu} norm={norm:2d} ({len(ref) / 16000:.0f} s): ERLE per 5 s: " + " ".join(f"{v:5.1f}" for v in per))
