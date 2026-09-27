"""Text-to-speech chain (C6):

  Azure Speech (free F0 tier, needs key) → edge-tts (unofficial, keyless, may
  break without notice) → Windows SAPI (built-in, local) → Kokoro ONNX
  (optional extra) → text-only.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import httpx
import structlog

from scar.config.settings import Settings
from scar.core.events import EventBus, ProviderFallback
from scar.providers.base import SpeechAudio
from scar.providers.capabilities import Catalog
from scar.providers.errors import AllProvidersFailed, ProviderError, ProviderErrorKind
from scar.providers.health import HealthTracker
from scar.providers.local.model_lifecycle import LocalModelManager
from scar.security.privacy import EgressTracker, PrivacyPolicy
from scar.security.secrets import SecretStore

log = structlog.get_logger("scar.tts")


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    import io

    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
        rate = w.getframerate()
    if channels == 2:
        import numpy as np

        arr = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2).mean(axis=1).astype(np.int16)
        frames = arr.tobytes()
    return frames, rate


class SapiTts:
    """Windows SAPI 5 synthesis to a WAV file (so playback stays interruptible)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        if os.name != "nt":
            return False
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            return False
        return True

    def synth_sync(self, text: str, voice_hint: str = "") -> SpeechAudio:
        import pythoncom
        import win32com.client

        with self._lock:
            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                if voice_hint:
                    voices = voice.GetVoices()
                    for i in range(voices.Count):
                        if voice_hint.lower() in voices.Item(i).GetDescription().lower():
                            voice.Voice = voices.Item(i)
                            break
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
                fmt.Type = 22  # SAFT22kHz16BitMono
                stream.Format = fmt
                fd, path = tempfile.mkstemp(suffix=".wav", prefix="scar_tts_")
                os.close(fd)
                try:
                    stream.Open(path, 3, False)  # SSFMCreateForWrite
                    voice.AudioOutputStream = stream
                    voice.Speak(text, 0)
                    stream.Close()
                    pcm, rate = wav_to_pcm(Path(path).read_bytes())
                finally:
                    Path(path).unlink(missing_ok=True)
            finally:
                pythoncom.CoUninitialize()
        return SpeechAudio(pcm16=pcm, sample_rate=rate, provider="sapi")


class KokoroTts:
    """Optional local neural TTS (install extra `kokoro`; needs model files in the data dir)."""

    def __init__(self, model_dir: Path, local: LocalModelManager | None) -> None:
        self.model_dir = model_dir
        self.local = local
        self._k: Any = None

    def available(self) -> tuple[bool, str]:
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False, "kokoro-onnx not installed (uv sync --extra kokoro)"
        if not (self.model_dir / "kokoro-v1.0.onnx").exists() or not (self.model_dir / "voices-v1.0.bin").exists():
            return False, f"Kokoro model files missing in {self.model_dir}"
        return True, ""

    def synth_sync(self, text: str, voice: str) -> SpeechAudio:
        import numpy as np
        from kokoro_onnx import Kokoro

        if self._k is None:
            self._k = Kokoro(str(self.model_dir / "kokoro-v1.0.onnx"), str(self.model_dir / "voices-v1.0.bin"))
            if self.local is not None:
                self.local.register_component("kokoro-tts", self.unload, 350.0)
        samples, rate = self._k.create(text, voice=voice or "af_heart", speed=1.0, lang="en-us")
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
        return SpeechAudio(pcm16=pcm, sample_rate=int(rate), provider="kokoro")

    def unload(self) -> None:
        self._k = None


class TtsService:
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
        self.sapi = SapiTts()
        self.kokoro = KokoroTts(settings.models_path / "kokoro", local)
        self.last_provider = ""

    def _chain(self) -> list[tuple[str, list[str]]]:
        chain = [(c.provider, c.models) for c in self.catalog.categories.get("tts", [])]
        pref = self.settings.tts_provider
        if pref and pref != "auto":
            if pref in ("none", "text"):
                return []
            chain.sort(key=lambda c: 0 if c[0] == pref else 1)
        if self.settings.local_inference_policy == "always":
            chain = [c for c in chain if c[0] in ("sapi", "kokoro")]
        return chain

    async def synthesize(self, text: str) -> SpeechAudio:
        attempts: list[ProviderError] = []
        previous = ""
        for provider, models in self._chain():
            voice = self.settings.tts_voice or (models[0] if models else "")
            if previous and previous != provider:
                self.bus.publish(ProviderFallback(category="tts", from_provider=previous, to_provider=provider,
                                                  reason=attempts[-1].kind.value if attempts else ""))
            if not self.health.get(provider, "tts").usable():
                continue
            t0 = time.perf_counter()
            try:
                audio = await self._synth(provider, text, voice)
            except ProviderError as err:
                err.provider = provider
                attempts.append(err)
                if err.kind != ProviderErrorKind.UNAVAILABLE:
                    self.health.failure(err)
                previous = provider
                continue
            self.health.success(provider, "tts", (time.perf_counter() - t0) * 1000)
            if provider in ("azure-speech", "edge-tts"):
                self.egress.record(provider, ["general"])
            self.last_provider = provider
            return audio
        raise AllProvidersFailed("tts", attempts)

    async def _synth(self, provider: str, text: str, voice: str) -> SpeechAudio:
        if provider == "azure-speech":
            key = self.secrets.get("AZURE_SPEECH_KEY")
            region = self.secrets.get_plain("AZURE_SPEECH_REGION")
            if key is None or not region:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, "missing AZURE_SPEECH_KEY / AZURE_SPEECH_REGION")
            ssml = (f"<speak version='1.0' xml:lang='en-US'><voice name='{escape(voice or 'en-US-AriaNeural')}'>"
                    f"{escape(text)}</voice></speak>")
            try:
                async with httpx.AsyncClient(timeout=20.0) as c:
                    r = await c.post(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                                     headers={"Ocp-Apim-Subscription-Key": key.get_secret_value(),
                                              "Content-Type": "application/ssml+xml",
                                              "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm", "User-Agent": "SCAR"},
                                     content=ssml.encode("utf-8"))
            except httpx.HTTPError as exc:
                raise ProviderError(ProviderErrorKind.TRANSIENT, str(exc)) from exc
            if r.status_code in (401, 403):
                raise ProviderError(ProviderErrorKind.AUTH, r.text[:200], status=r.status_code)
            if r.status_code == 429:
                raise ProviderError(ProviderErrorKind.RATE_LIMIT, r.text[:200], status=429)
            if r.status_code >= 400:
                raise ProviderError(ProviderErrorKind.TRANSIENT, r.text[:200], status=r.status_code)
            pcm, rate = wav_to_pcm(r.content)
            return SpeechAudio(pcm16=pcm, sample_rate=rate, provider="azure-speech")
        if provider == "edge-tts":
            try:
                import edge_tts
            except ImportError as exc:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, "edge-tts not installed") from exc
            try:
                comm = edge_tts.Communicate(text, voice or "en-US-AriaNeural", connect_timeout=8, receive_timeout=30)
                chunks: list[bytes] = []
                async for chunk in comm.stream():
                    if chunk.get("type") == "audio" and chunk.get("data"):
                        chunks.append(chunk["data"])
            except Exception as exc:
                raise ProviderError(ProviderErrorKind.TRANSIENT, f"edge-tts failed: {exc}") from exc
            if not chunks:
                raise ProviderError(ProviderErrorKind.MALFORMED, "edge-tts returned no audio")
            return SpeechAudio(encoded=b"".join(chunks), encoding="mp3", provider="edge-tts")
        if provider == "sapi":
            if not self.sapi.available():
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, "Windows SAPI not available")
            try:
                return await asyncio.to_thread(self.sapi.synth_sync, text, self.settings.tts_voice if self.settings.tts_provider == "sapi" else "")
            except Exception as exc:
                raise ProviderError(ProviderErrorKind.TRANSIENT, f"SAPI failed: {exc}") from exc
        if provider == "kokoro":
            ok, why = self.kokoro.available()
            if not ok:
                raise ProviderError(ProviderErrorKind.UNAVAILABLE, why)
            return await asyncio.to_thread(self.kokoro.synth_sync, text, voice)
        raise ProviderError(ProviderErrorKind.UNAVAILABLE, f"unknown TTS provider {provider}")
