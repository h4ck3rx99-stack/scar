"""Voice activity detection: Silero VAD (ONNX, bundled with faster-whisper) with an energy fallback,
plus utterance endpointing."""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from scar.voice.audio_io import FRAME_SAMPLES, SAMPLE_RATE, rms_level

CONTEXT = 64


class SileroVad:
    def __init__(self) -> None:
        import faster_whisper
        import onnxruntime as ort

        path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(CONTEXT, dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = frame.astype(np.float32) / 32768.0
        if x.shape[0] != FRAME_SAMPLES:
            x = np.pad(x, (0, max(0, FRAME_SAMPLES - x.shape[0])))[:FRAME_SAMPLES]
        inp = np.concatenate([self.context, x])[None, :]
        self.context = x[-CONTEXT:]
        out, self.h, self.c = self.session.run(None, {"input": inp, "h": self.h, "c": self.c})
        return float(np.asarray(out).reshape(-1)[0])


class EnergyVad:
    def __init__(self, threshold: float = 0.015) -> None:
        self.threshold = threshold

    def reset(self) -> None:
        return None

    def prob(self, frame: np.ndarray) -> float:
        return min(1.0, rms_level(frame) / (self.threshold * 2))


def make_vad() -> SileroVad | EnergyVad:
    try:
        return SileroVad()
    except Exception:  # noqa: BLE001 - onnxruntime/model missing: degrade to energy detection
        return EnergyVad()


@dataclass
class Endpointer:
    """Collects frames into an utterance: starts on speech, ends after trailing silence or max length."""

    vad: SileroVad | EnergyVad
    threshold: float = 0.5
    start_frames: int = 3  # ~100 ms of speech to start
    end_silence_s: float = 0.8
    max_seconds: float = 20.0
    pre_roll_frames: int = 10

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self.frames: list[np.ndarray] = []
        self.pre: list[np.ndarray] = []
        self.speech_run = 0
        self.silence_run = 0
        self.started = False

    def feed(self, frame: np.ndarray) -> bytes | None:
        """Returns PCM16 bytes when an utterance is complete."""
        p = self.vad.prob(frame)
        speech = p >= self.threshold
        if not self.started:
            self.pre.append(frame)
            del self.pre[: -self.pre_roll_frames]
            self.speech_run = self.speech_run + 1 if speech else 0
            if self.speech_run >= self.start_frames:
                self.started = True
                self.frames = list(self.pre)
            return None
        self.frames.append(frame)
        self.silence_run = 0 if speech else self.silence_run + 1
        dur = len(self.frames) * FRAME_SAMPLES / SAMPLE_RATE
        if self.silence_run * FRAME_SAMPLES / SAMPLE_RATE >= self.end_silence_s or dur >= self.max_seconds:
            pcm = np.concatenate(self.frames).astype(np.int16).tobytes()
            self.reset()
            return pcm
        return None
