"""Streaming Silero VAD (ONNX, 512-sample chunks at 16 kHz). ~0.1 ms per chunk on CPU."""
import numpy as np
import onnxruntime as ort

from jarvis import config


class SileroVAD:
    def __init__(self, path=config.VAD_MODEL):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.sr = np.array(config.SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self):
        self.h = np.zeros((2, 1, 64), dtype=np.float32)
        self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        """chunk: float32 [-1, 1], 512 samples. Returns speech probability."""
        out, self.h, self.c = self.sess.run(
            None, {"input": chunk.reshape(1, -1), "sr": self.sr, "h": self.h, "c": self.c}
        )
        return float(out[0][0])
