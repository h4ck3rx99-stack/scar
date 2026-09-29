"""faster-whisper STT. Tries CUDA float16, falls back to CPU int8.

Decoding is greedy with no temperature fallback: on noisy audio the default fallback re-decodes up to 5
times, which turned 0.3 s into 4-6 s. Repetition loops ("V troubleshooter, V troubleshooter...") are blocked
with no_repeat_ngram_size and dropped by a compression-ratio check.
"""
import logging
import zlib

import numpy as np

from jarvis import config
from jarvis.gpu import add_nvidia_dll_dirs

log = logging.getLogger("stt")

# App names the recogniser should spell correctly.
HOTWORDS = "Scar, Jarvis, Chrome, Opera, VS Code, Spotify, YouTube, Notepad, Paint, Discord, WhatsApp, Instagram"

DECODE = dict(
    language="en", beam_size=1, temperature=0.0, vad_filter=False, condition_on_previous_text=False,
    without_timestamps=True, no_repeat_ngram_size=3, initial_prompt=HOTWORDS,
)


def _compression_ratio(text: str) -> float:
    b = text.encode("utf-8")
    return len(b) / max(1, len(zlib.compress(b)))


class WhisperSTT:
    def __init__(self):
        add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        # int8 weights + fp16 compute: half the VRAM of float16 at practically the same accuracy,
        # which leaves room for the LLM's vision encoder on an 8 GB card.
        attempts = [("cuda", "int8_float16"), ("cuda", "float16"), ("cpu", "int8")]
        if config.WHISPER_DEVICE == "cpu":
            attempts = [("cpu", "int8")]
        last = None
        for device, ctype in attempts:
            try:
                self.model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=ctype)
                # A real decode proves the kernels work on this GPU, not just that the DLLs loaded.
                self.transcribe(np.zeros(16000, dtype=np.float32))
                self.device = f"{device}/{ctype}"
                log.info("whisper %s on %s", config.WHISPER_MODEL.replace("\\", "/").split("/")[-1], self.device)
                return
            except Exception as e:  # noqa: BLE001 - any failure means try the next device
                last = e
                log.warning("whisper %s failed: %s", device, e)
        raise RuntimeError(f"no usable whisper backend: {last}")

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(audio.astype(np.float32), **DECODE)
        kept = []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            # Silence/noise hallucinations and repetition loops.
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.7:
                continue
            if len(text) > 24 and _compression_ratio(text) > 2.4:
                continue
            kept.append(text)
        return " ".join(kept).strip()
