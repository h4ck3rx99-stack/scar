"""Convergence of the linear echo filter alone, per second, on a simulated echo (no user speech)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.aec import BLOCK, EchoCanceller  # noqa: E402

rng = np.random.default_rng(1)
# Speech-like reference: noise shaped by a slow envelope, 8 s.
n = 16000 * 8
ref = (rng.standard_normal(n) * (0.5 + 0.5 * np.sin(np.arange(n) / 1600) ** 2)).astype(np.float32) * 0.1
delay = int(0.18 * 16000)
rir = rng.standard_normal(3200) * np.exp(-np.arange(3200) / 900.0)
rir[0] += 3.0
rir = np.concatenate([np.zeros(delay), rir]) * 0.08
mic = np.convolve(ref, rir)[:n].astype(np.float32)

for mu, norm in ((0.5, 16), (0.1, 1), (0.2, 1), (0.3, 1), (0.2, 2), (0.4, 4)):
    aec = EchoCanceller(mu=mu)
    aec.norm = norm
    lin = []
    for i in range(0, n - BLOCK, BLOCK):
        aec.process(mic[i:i + BLOCK], ref[i:i + BLOCK])
        lin.append(aec.last_linear)
    lin = np.concatenate(lin)
    per_s = [10 * np.log10(np.mean(mic[s:s + 16000] ** 2) / (np.mean(lin[s:s + 16000] ** 2) + 1e-12)) for s in range(0, len(lin) - 16000, 16000)]
    print(f"mu={mu} norm={norm:2d}: ERLE per second: " + " ".join(f"{v:5.1f}" for v in per_s))
