"""Local ONNX embeddings via fastembed (CPU). Lazy-loaded and idle-unloaded."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import numpy as np

from scar.config.settings import Settings
from scar.core.errors import CapabilityUnavailable
from scar.providers.local.model_lifecycle import LocalModelManager


class EmbeddingService:
    def __init__(self, settings: Settings, local: LocalModelManager | None) -> None:
        self.settings = settings
        self.model_name = settings.embeddings_model
        self.local = local
        self._model: Any = None
        self._lock = threading.Lock()
        self._dim: int | None = None
        self.cache_dir = settings.models_path / "fastembed"

    @property
    def enabled(self) -> bool:
        return self.settings.embeddings_provider not in ("none", "off") and self.settings.memory_backend != "fts_only"

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:
                    raise CapabilityUnavailable("fastembed is not installed", "docs/memory.md") from exc
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                try:
                    self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir), threads=2)
                except Exception as exc:
                    raise CapabilityUnavailable(f"embedding model {self.model_name} could not be loaded: {exc}",
                                                "docs/memory.md") from exc
                if self.local is not None:
                    self.local.register_component(f"embeddings:{self.model_name}", self.unload, 130.0)
            elif self.local is not None:
                self.local.component_used(f"embeddings:{self.model_name}")
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._model = None

    def embed_sync(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        vecs = np.array(list(model.embed(texts, batch_size=16)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = vecs / norms
        self._dim = int(vecs.shape[1])
        return vecs

    async def embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.embed_sync, texts)

    @property
    def dim(self) -> int | None:
        return self._dim

    @property
    def loaded(self) -> bool:
        return self._model is not None
