"""Kokoro-82M ONNX text-to-speech (24 kHz mono float32). CUDA when available (~160 ms/sentence), else CPU."""
import logging
import re

import numpy as np

from jarvis import config
from jarvis.gpu import add_nvidia_dll_dirs

log = logging.getLogger("tts")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]")


def clean_for_speech(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_`#>]+", "", text)          # markdown the LLM may emit
    text = re.sub(r"https?://\S+", "a link", text)
    return re.sub(r"\s+", " ", text).strip()


class KokoroTTS:
    def __init__(self):
        add_nvidia_dll_dirs()
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        providers = []
        if "CUDAExecutionProvider" in ort.get_available_providers():
            # Cap the CUDA arena: by default it grows and never gives memory back.
            providers.append(("CUDAExecutionProvider", {"gpu_mem_limit": 600 * 1024 * 1024, "arena_extend_strategy": "kSameAsRequested"}))
        providers.append("CPUExecutionProvider")
        sess = ort.InferenceSession(str(config.KOKORO_MODEL), providers=providers)
        self.device = sess.get_providers()[0]
        log.info("kokoro on %s", self.device)
        self.kokoro = Kokoro.from_session(sess, str(config.KOKORO_VOICES))
        self.voice = config.KOKORO_VOICE
        self.speed = config.KOKORO_SPEED
        self.synth("Ready.")  # first CUDA run compiles kernels; keep it off the user's first reply

    def synth(self, text: str):
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.float32), 24000
        samples, sr = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        return samples.astype(np.float32), sr
