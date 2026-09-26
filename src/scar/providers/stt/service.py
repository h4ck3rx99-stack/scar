"""Speech-to-text chain: cloud Whisper (Groq free tier) → local faster-whisper (CPU int8)."""

from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from typing import Any

import numpy as np
import structlog

from scar.config.settings import Settings
from scar.core.events import EventBus, ProviderFallback
from scar.providers.base import Transcription
from scar.providers.capabilities import Catalog
from scar.providers.errors import AllProvidersFailed, ProviderError, ProviderErrorKind
from scar.providers.health import HealthTracker
from scar.providers.llm.openai_compat import OpenAICompatClient
from scar.providers.local.model_lifecycle import LocalModelManager
from scar.security.privacy import EgressTracker, PrivacyPolicy
from scar.security.secrets import SecretStore

log = structlog.get_logger("scar.stt")


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class LocalWhisper:
    """faster-whisper on CPU (int8). Lazy-loaded, idle-unloaded via the lifecycle manager."""

    def __init__(self, model_size: str, download_root: str, local: LocalModelManager | None) -> None:
        self.model_size = model_size
        self.download_root = download_root
        self.local = local
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8",
                                           download_root=self.download_root, cpu_threads=4)
                if self.local is not None:
                    self.local.register_component(f"faster-whisper:{self.model_size}", self.unload, 150.0)
            elif self.local is not None:
                self.local.component_used(f"faster-whisper:{self.model_size}")
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def transcribe_sync(self, pcm16: bytes, sample_rate: int, language: str | None) -> Transcription:
        model = self._load()
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if sample_rate != 16000:
            n = int(len(audio) * 16000 / sample_rate)
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)
        segments, info = model.transcribe(audio, language=language, beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcription(text=text, language=info.language, duration_s=info.duration, provider="faster-whisper",
                             model=self.model_size)


class SttService:
    def __init__(self, settings: Settings, secrets: SecretStore, health: HealthTracker, bus: EventBus,
                 privacy: PrivacyPolicy, egress: EgressTracker, local: LocalModelManager | None,
                 catalog: Catalog | None = None) -> None:
        self.settings = settings
        self.secrets = secrets
        self.health = health
        self.bus = bus
        self.privacy = privacy
        self.egress = egress
        self.catalog = catalog or Catalog.load()
        self.local_whisper = LocalWhisper(settings.local_stt_model, str(settings.data_path / "models" / "whisper"), local)
        self._groq: OpenAICompatClient | None = None
        self.last_provider = ""

    def _chain(self) -> list[tuple[str, list[str]]]:
        chain = [(c.provider, c.models) for c in self.catalog.categories.get("stt", [])]
        pref = self.settings.stt_provider
        if pref and pref != "auto":
            chain.sort(key=lambda c: 0 if c[0] == pref else 1)
            if pref == "faster-whisper" and self.settings.stt_model:
                chain = [(p, [self.settings.stt_model] if p == pref else m) for p, m in chain]
        if self.settings.local_inference_policy == "never":
            chain = [c for c in chain if c[0] != "faster-whisper"]
        return chain

    async def transcribe(self, pcm16: bytes, sample_rate: int = 16000, language: str | None = None) -> Transcription:
        attempts: list[ProviderError] = []
        cloud_ok = self.privacy.cloud_allowed("audio")
        previous = ""
        for provider, models in self._chain():
            if provider == "faster-whisper":
                if previous:
                    self.bus.publish(ProviderFallback(category="stt", from_provider=previous, to_provider="faster-whisper",
                                                      reason=attempts[-1].kind.value if attempts else ""))
                try:
                    result = await asyncio.to_thread(self.local_whisper.transcribe_sync, pcm16, sample_rate, language)
                    self.last_provider = "faster-whisper"
                    return result
                except (OSError, RuntimeError, ValueError) as exc:
                    attempts.append(ProviderError(ProviderErrorKind.UNAVAILABLE, str(exc), provider="faster-whisper"))
                    continue
            if provider == "groq":
                if not cloud_ok:
                    attempts.append(ProviderError(ProviderErrorKind.PRIVACY, "audio is local_only", provider="groq"))
                    continue
                key = self.secrets.get("GROQ_API_KEY")
                if key is None:
                    attempts.append(ProviderError(ProviderErrorKind.UNAVAILABLE, "missing GROQ_API_KEY", provider="groq"))
                    continue
                if self._groq is None:
                    self._groq = OpenAICompatClient("groq", "https://api.groq.com/openai/v1", key)
                wav = pcm16_to_wav(pcm16, sample_rate)
                for model in ([self.settings.stt_model] if self.settings.stt_model and self.settings.stt_provider == "groq" else models):
                    if not self.health.get("groq", model).usable():
                        continue
                    previous = f"groq/{model}"
                    t0 = time.perf_counter()
                    try:
                        data = await self._groq.transcribe(model, wav, language)
                    except ProviderError as err:
                        err.provider, err.model = "groq", model
                        self.health.failure(err)
                        attempts.append(err)
                        if err.kind in (ProviderErrorKind.AUTH, ProviderErrorKind.QUOTA):
                            break
                        continue
                    self.health.success("groq", model, (time.perf_counter() - t0) * 1000)
                    self.egress.record("groq", ["audio"])
                    self.last_provider = f"groq/{model}"
                    return Transcription(text=str(data.get("text", "")).strip(), language=data.get("language"),
                                         duration_s=data.get("duration"), provider="groq", model=model)
        raise AllProvidersFailed("stt", attempts)

    async def aclose(self) -> None:
        if self._groq is not None:
            await self._groq.aclose()
