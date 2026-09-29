"""small.en vs large-v3-turbo, greedy (no temperature fallback), on clean and noisy speech."""
import sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.gpu import add_nvidia_dll_dirs
add_nvidia_dll_dirs()
from faster_whisper import WhisperModel
from jarvis.tts.kokoro_tts import KokoroTTS

tts = KokoroTTS()
def say(text, voice):
    tts.voice = voice
    a, sr = tts.synth(text)
    return np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)

cmd = say("Close this tab and open YouTube.", "am_adam")
babble = np.concatenate([say("You are supposed to take the last index and compare the digits one by one.", "bf_emma"),
                         say("Why twenty into four, because of your discount.", "bm_george")])
n = min(len(cmd), len(babble))
noisy = cmd.copy(); noisy[:n] += 0.35 * babble[:n]
long_babble = np.tile(babble, 2)
KW = dict(language="en", beam_size=1, temperature=0.0, condition_on_previous_text=False, without_timestamps=True,
          vad_filter=False, no_repeat_ngram_size=3, initial_prompt="Jarvis, Chrome, YouTube, Notepad")
for name in ("small.en", "large-v3-turbo"):
    m = WhisperModel(f"models/whisper/{name}", device="cuda", compute_type="float16")
    m.transcribe(cmd, **KW)
    for label, audio in (("clean cmd", cmd), ("noisy cmd", noisy), ("10s babble", long_babble)):
        t = time.perf_counter(); segs, info = m.transcribe(audio, **KW); segs = list(segs)
        ms = (time.perf_counter() - t) * 1000
        text = " ".join(s.text.strip() for s in segs)
        lp = min((s.avg_logprob for s in segs), default=0); ns = max((s.no_speech_prob for s in segs), default=0)
        print(f"{name:15s} {label:11s} {ms:6.0f} ms  logprob={lp:5.2f} nospeech={ns:.2f}  {text[:70]!r}")
    del m
