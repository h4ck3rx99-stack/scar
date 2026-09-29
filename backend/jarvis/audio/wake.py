"""openWakeWord "hey jarvis" detector (ONNX). Consumes 80 ms int16 frames."""
import numpy as np
from openwakeword.model import Model

from jarvis import config

FRAME = 1280  # 80 ms at 16 kHz, openWakeWord's native step


class WakeWord:
    def __init__(self, threshold=config.WAKE_THRESHOLD):
        mdir = config.WAKE_MODEL.parent
        self.model = Model(
            wakeword_models=[str(config.WAKE_MODEL)],
            inference_framework="onnx",
            melspec_model_path=str(mdir / "melspectrogram.onnx"),
            embedding_model_path=str(mdir / "embedding_model.onnx"),
        )
        self.name = next(iter(self.model.models))
        self.threshold = threshold
        self._buf = np.zeros(0, dtype=np.int16)
        self.last_score = 0.0

    def process(self, frame: np.ndarray) -> bool:
        scores = self.model.predict(frame)
        self.last_score = float(scores[self.name])
        return self.last_score >= self.threshold

    def feed(self, pcm16: np.ndarray) -> bool:
        """Accepts any block size; returns True if the wake word fired in this block."""
        self._buf = np.concatenate([self._buf, pcm16])
        fired = False
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            fired |= self.process(frame)
        return fired

    def reset(self):
        self.model.reset()
        self._buf = np.zeros(0, dtype=np.int16)
