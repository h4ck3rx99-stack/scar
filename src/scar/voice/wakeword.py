"""Wake-word detection with openWakeWord (ONNX, CPU). Runs only while voice mode is on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from scar.core.errors import CapabilityUnavailable

FEATURE_MODELS = ("melspectrogram", "embedding_model")


class WakeWord:
    def __init__(self, name: str, model_dir: Path, threshold: float = 0.5) -> None:
        self.name = name
        self.model_dir = model_dir
        self.threshold = threshold
        self._model: Any = None
        self._buf = np.zeros(0, dtype=np.int16)

    def _paths(self) -> tuple[Path, Path, Path]:
        d = self.model_dir
        return d / f"{self.name}_v0.1.onnx", d / "melspectrogram.onnx", d / "embedding_model.onnx"

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
        except ImportError as exc:
            raise CapabilityUnavailable("openWakeWord is not installed", "docs/voice.md") from exc
        ww, mel, emb = self._paths()
        if not (ww.exists() and mel.exists() and emb.exists()):
            self.model_dir.mkdir(parents=True, exist_ok=True)
            try:
                download_models([self.name], target_directory=str(self.model_dir))
            except Exception as exc:  # noqa: BLE001 - network/download errors
                raise CapabilityUnavailable(f"could not download wake-word model {self.name!r}: {exc}", "docs/voice.md") from exc
        if not ww.exists():
            raise CapabilityUnavailable(f"unknown wake word {self.name!r} (try hey_jarvis, alexa, hey_mycroft)", "docs/voice.md")
        self._model = Model(wakeword_models=[str(ww)], inference_framework="onnx", melspec_model_path=str(mel),
                            embedding_model_path=str(emb))

    def unload(self) -> None:
        self._model = None

    def feed(self, frame: np.ndarray) -> bool:
        """Feed 16 kHz int16 audio; True when the wake word is detected."""
        if self._model is None:
            self.load()
        self._buf = np.concatenate([self._buf, frame])
        hit = False
        while len(self._buf) >= 1280:
            chunk, self._buf = self._buf[:1280], self._buf[1280:]
            scores = self._model.predict(chunk)
            if any(float(v) >= self.threshold for v in scores.values()):
                hit = True
        if hit:
            self._model.reset()
        return hit
