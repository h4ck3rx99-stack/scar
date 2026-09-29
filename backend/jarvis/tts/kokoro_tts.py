"""Kokoro-82M ONNX text-to-speech (24 kHz mono float32). CUDA when available (~160 ms/sentence), else CPU.

If the GPU run fails (out of memory while the LLM is busy, driver hiccup) the same sentence is retried on a
CPU session, so Scar never goes silent. Very long sentences are split at commas first.
"""
import logging
import re

import numpy as np

from jarvis import config
from jarvis.gpu import add_nvidia_dll_dirs

log = logging.getLogger("tts")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿]")
MAX_CHUNK = 220  # characters; longer text is split at clause boundaries


def clean_for_speech(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_`#>]+", "", text)          # markdown the LLM may emit
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"(\d)\s*%", r"\1 percent", text)
    text = re.sub(r"(\d)\s*°C", r"\1 degrees", text)
    return re.sub(r"\s+", " ", text).strip()


def split_long(text: str):
    if len(text) <= MAX_CHUNK:
        return [text]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[,;:])\s+", text):
        if cur and len(cur) + len(piece) > MAX_CHUNK:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts


class KokoroTTS:
    def __init__(self):
        add_nvidia_dll_dirs()
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        self._ort, self._Kokoro = ort, Kokoro
        providers = []
        if "CUDAExecutionProvider" in ort.get_available_providers():
            # Grow the CUDA arena only as needed (the default doubles and never gives memory back).
            providers.append(("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}))
        providers.append("CPUExecutionProvider")
        sess = ort.InferenceSession(str(config.KOKORO_MODEL), providers=providers)
        self.device = sess.get_providers()[0]
        log.info("kokoro on %s", self.device)
        self.kokoro = Kokoro.from_session(sess, str(config.KOKORO_VOICES))
        self._cpu = None
        self.voice = config.KOKORO_VOICE
        self.speed = config.KOKORO_SPEED
        self.synth("Ready.")  # first CUDA run compiles kernels; keep it off the user's first reply

    def _cpu_kokoro(self):
        if self._cpu is None:
            sess = self._ort.InferenceSession(str(config.KOKORO_MODEL), providers=["CPUExecutionProvider"])
            self._cpu = self._Kokoro.from_session(sess, str(config.KOKORO_VOICES))
        return self._cpu

    def _create(self, text):
        try:
            return self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        except Exception as e:  # noqa: BLE001 - GPU OOM etc.: retry on CPU rather than stay silent
            log.warning("GPU TTS failed (%s); retrying on CPU", str(e)[:120])
            return self._cpu_kokoro().create(text, voice=self.voice, speed=self.speed, lang="en-us")

    def synth(self, text: str):
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.float32), 24000
        chunks, sr = [], 24000
        for part in split_long(text):
            samples, sr = self._create(part)
            chunks.append(samples.astype(np.float32))
        return np.concatenate(chunks), sr
