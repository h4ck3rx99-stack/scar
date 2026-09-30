"""How well does the "Scar" keyword spotter hear its name, and how often does it fire on other words?
Synthesizes speech with Kokoro (several voices, speeds), adds room noise, and feeds it block by block.
Usage: python bench/kws_eval.py [threshold ...]"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from jarvis import config  # noqa: E402
from jarvis.audio.kws import NameSpotter  # noqa: E402

POS = ["Scar.", "Hey Scar.", "Scar, open YouTube.", "Okay Scar, what's the weather?", "Hey Scar, stop.",
       "Scar standby.", "Scar, play some music.", "Scar!"]
NEG = ["Scarf.", "Oscar is here.", "That movie was scary.", "My car is parked outside.", "He has a scar on his arm.",
       "Start the car.", "Discard it.", "Open YouTube and search for lofi.", "What's the weather today?",
       "Scott is my friend.", "The scarecrow stood in the field.", "Let's go to the store later.",
       "I'm scared of the dark.", "Can you pass the salt?", "We should schedule a meeting.", "Skype call at five."]
VOICES = ["am_michael", "af_heart", "bm_george", "af_bella", "am_adam", "bf_emma"]


def synth():
    from jarvis.tts.kokoro_tts import KokoroTTS
    tts = KokoroTTS()
    rng = np.random.default_rng(0)
    out = {"pos": [], "neg": []}
    for kind, texts in (("pos", POS), ("neg", NEG)):
        for i, text in enumerate(texts):
            for v in VOICES[: 6 if kind == "pos" else 3]:
                tts.voice, tts.speed = v, float(rng.choice([0.9, 1.0, 1.15]))
                samples, sr = tts.synth(text)
                idx = np.arange(0, len(samples), sr / 16000)
                a = np.interp(idx, np.arange(len(samples)), samples).astype(np.float32) * float(rng.uniform(0.3, 1.0))
                a = np.concatenate([np.zeros(8000, np.float32), a, np.zeros(12000, np.float32)])
                a += (rng.standard_normal(len(a)) * 0.004).astype(np.float32)   # room noise
                out[kind].append((text, v, a))
    return out


def run(data, thr):
    ks = NameSpotter(threshold=thr)
    res = {}
    for kind, items in data.items():
        hits = []
        for text, v, a in items:
            ks.reset()
            got = ""
            for i in range(0, len(a) - 512, 512):
                got = ks.feed(a[i:i + 512]) or got
            hits.append((text, v, got))
        res[kind] = hits
    tp = sum(1 for _, _, g in res["pos"] if g)
    fp = [(t, v, g) for t, v, g in res["neg"] if g]
    missed = [(t, v) for t, v, g in res["pos"] if not g]
    print(f"threshold {thr}: heard {tp}/{len(res['pos'])} name calls, false alarms {len(fp)}/{len(res['neg'])}")
    for t, v, g in fp:
        print("   false:", t, v, g)
    for t, v in missed[:8]:
        print("   missed:", t, v)


if __name__ == "__main__":
    data = synth()
    for thr in [float(x) for x in sys.argv[1:]] or [config.KWS_THRESHOLD]:
        run(data, thr)
