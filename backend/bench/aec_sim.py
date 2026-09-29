"""Simulated speaker->room->mic echo to check the echo canceller: how much of Scar's voice it removes (ERLE),
and whether a user talking over it survives. Scar = one Kokoro voice, user = another."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.aec import BLOCK, EchoCanceller, ReferenceBuffer  # noqa: E402
from jarvis.audio.vad import SileroVAD  # noqa: E402
from jarvis.tts.kokoro_tts import KokoroTTS  # noqa: E402

tts = KokoroTTS()
tts.voice = "am_michael"
scar24, _ = tts.synth("The weather in Bengaluru is twenty five degrees with light clouds. Tomorrow looks similar, "
                      "with a chance of rain in the evening, so keep an umbrella handy just in case.")
tts.voice = "af_heart"
user24, _ = tts.synth("Scar, stop.")
to16 = lambda a: np.interp(np.arange(0, len(a), 1.5), np.arange(len(a)), a).astype(np.float32)  # noqa: E731
scar16, user16 = to16(scar24), to16(user24)

rng = np.random.default_rng(0)
delay = int(0.18 * 16000)                                   # 180 ms speaker+mic latency
rir = rng.standard_normal(3200) * np.exp(-np.arange(3200) / 900.0)  # ~200 ms reverb tail
rir[0] += 3.0
rir = np.concatenate([np.zeros(delay), rir]) * 0.08
echo = np.convolve(scar16, rir)[: len(scar16)]
mic = echo + 0.002 * rng.standard_normal(len(echo))
u0 = int(len(mic) * 0.7)                                    # user interrupts 70% of the way through
mic[u0:u0 + len(user16)] += 0.5 * user16[: len(mic) - u0]

ref = ReferenceBuffer(out_sr=24000)
aec = EchoCanceller()
vad = SileroVAD()
out, vad_raw, vad_clean, lin_out = [], [], [], []
for i in range(0, len(mic) - BLOCK, BLOCK):
    ref.push(scar24[int(i * 1.5): int((i + BLOCK) * 1.5)])
    r = ref.take(BLOCK)
    e = aec.process(mic[i:i + BLOCK], r)
    out.append(e)
    lin_out.append(aec.last_linear)
    vad_raw.append(vad(mic[i:i + BLOCK].astype(np.float32)))
vad.reset()
for e in out:
    vad_clean.append(vad(e))
out = np.concatenate(out)
seg = slice(len(out) // 3, u0 - BLOCK)                      # after convergence, before the user speaks
erle = 10 * np.log10(np.mean(mic[seg] ** 2) / np.mean(out[seg] ** 2))
lin_out = np.concatenate(lin_out)
print(f"linear filter alone: {10 * np.log10(np.mean(mic[seg] ** 2) / np.mean(lin_out[seg] ** 2)):.1f} dB")
ub, ue = u0 // BLOCK, (u0 + len(user16)) // BLOCK
echo_only = [p for k, p in enumerate(vad_clean) if len(out) // 3 // BLOCK < k < ub]
print(f"echo removed (ERLE): {erle:.1f} dB")
print(f"VAD fires on Scar's echo: raw {np.mean(np.array(vad_raw[len(out)//3//BLOCK:ub]) > 0.6):.0%}  "
      f"after AEC {np.mean(np.array(echo_only) > 0.6):.0%}")
print(f"VAD hears the user's 'Scar, stop' after AEC: {np.mean(np.array(vad_clean[ub:ue]) > 0.6):.0%} of its blocks")
