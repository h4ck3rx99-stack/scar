"""Phase-1 toolchain benchmark: VAD, wake word, STT, LLM, TTS latency on this machine.

Run: .venv\\Scripts\\python bench\\bench_all.py   (llama-server must be running on :8080)
"""
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis import config  # noqa: E402


def timed(fn, n=5):
    out, ts = None, []
    for _ in range(n):
        t = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t) * 1000)
    return out, statistics.median(ts)


def main():
    results = {}

    from jarvis.audio.vad import SileroVAD
    vad = SileroVAD()
    chunk = np.zeros(512, dtype=np.float32)
    _, results["vad_ms_per_chunk"] = timed(lambda: vad(chunk), 50)

    from jarvis.audio.wake import WakeWord
    ww = WakeWord()
    frame = np.zeros(1280, dtype=np.int16)
    _, results["wake_ms_per_80ms_frame"] = timed(lambda: ww.process(frame), 30)

    from jarvis.tts.kokoro_tts import KokoroTTS
    tts = KokoroTTS()
    tts.synth("warm up")
    (pcm, sr), results["tts_first_sentence_ms"] = timed(lambda: tts.synth("Minimizing Chrome for you."), 3)

    from jarvis.stt.whisper_stt import WhisperSTT
    stt = WhisperSTT()
    # Kokoro output (24 kHz) resampled to 16 kHz gives a realistic utterance for STT.
    speech = tts.synth("Hey Jarvis, minimize the Chrome window and open YouTube.")[0]
    speech16 = np.interp(np.arange(0, len(speech), 24000 / 16000), np.arange(len(speech)), speech).astype(np.float32)
    stt.transcribe(speech16)
    text, results["stt_ms_3s_utterance"] = timed(lambda: stt.transcribe(speech16), 3)
    results["stt_text"] = text
    results["stt_device"] = stt.device
    results["tts_device"] = tts.device

    import httpx
    body = {
        "messages": [{"role": "user", "content": "In one short sentence, what is the capital of France?"}],
        "stream": True, "max_tokens": 60,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    ttfts, tps = [], []
    for _ in range(3):
        t0 = time.perf_counter()
        first, n = None, 0
        with httpx.stream("POST", f"{config.LLM_URL}/chat/completions", json=body, timeout=60) as r:
            for line in r.iter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                if delta:
                    n += 1
                    first = first or time.perf_counter()
        end = time.perf_counter()
        ttfts.append((first - t0) * 1000)
        tps.append(n / max(end - first, 1e-3))
    results["llm_ttft_ms"] = statistics.median(ttfts)
    results["llm_tokens_per_s"] = statistics.median(tps)

    for k, v in results.items():
        print(f"{k:28s} {v:.1f}" if isinstance(v, float) else f"{k:28s} {v}")
    out = Path(__file__).with_name("RESULTS.md")
    out.write_text("# Benchmark results\n\n" + "\n".join(f"- **{k}**: {v:.1f}" if isinstance(v, float) else f"- **{k}**: {v}" for k, v in results.items()) + "\n")


if __name__ == "__main__":
    main()
