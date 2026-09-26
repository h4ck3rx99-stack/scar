"""Audio input/output (C10): device selection, microphone capture, interruptible playback.

Capture is 16 kHz mono int16 in 32 ms frames, delivered through a bounded queue.
Playback of PCM goes through sounddevice; encoded audio (MP3 from edge-tts) is
played through the Windows MCI player. Both can be stopped immediately (barge-in).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms: the Silero VAD window


def list_devices() -> list[dict[str, Any]]:
    import sounddevice as sd

    default_in, default_out = sd.default.device
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        out.append({"index": i, "name": d["name"], "inputs": d["max_input_channels"], "outputs": d["max_output_channels"],
                    "hostapi": apis[d["hostapi"]]["name"], "default": i in (default_in, default_out),
                    "samplerate": d["default_samplerate"]})
    return out


def resolve_device(spec: str, kind: str) -> int | None:
    """``spec`` is empty (system default), an index, or a case-insensitive name substring."""
    if not spec:
        return None
    if spec.strip().isdigit():
        return int(spec)
    key = "inputs" if kind == "input" else "outputs"
    matches = [d for d in list_devices() if d[key] > 0 and spec.lower() in d["name"].lower()]
    # prefer WASAPI/MME entries that open reliably at 16 kHz via resampling
    matches.sort(key=lambda d: (d["hostapi"] != "MME", d["hostapi"] != "Windows WASAPI"))
    if not matches:
        raise ValueError(f"no {kind} device matching {spec!r}")
    return int(matches[0]["index"])


class Microphone:
    """Continuous capture into a bounded queue of int16 frames. Frames are dropped while muted (half-duplex)."""

    def __init__(self, device: int | None, max_queue: int = 400) -> None:
        self.device = device
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue)
        self.muted = threading.Event()
        self.error: str | None = None
        self._stream: Any = None
        self._native_rate = SAMPLE_RATE
        self._resample_buf = np.zeros(0, dtype=np.float32)

    def start(self) -> None:
        import sounddevice as sd

        try:
            info = sd.query_devices(self.device, "input")
            rate = SAMPLE_RATE
            try:
                sd.check_input_settings(device=self.device, samplerate=SAMPLE_RATE, channels=1, dtype="int16")
            except Exception:  # noqa: BLE001 - device does not support 16 kHz: capture natively and resample
                rate = int(info["default_samplerate"])
            self._native_rate = rate
            block = int(FRAME_SAMPLES * rate / SAMPLE_RATE)
            self._stream = sd.InputStream(device=self.device, samplerate=rate, channels=1, dtype="int16", blocksize=block,
                                          callback=self._callback)
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudio raises many error types
            self.error = str(exc)
            raise

    def _callback(self, indata: np.ndarray, _frames: int, _time: Any, status: Any) -> None:
        if status and status.input_overflow:
            self.error = "input overflow"
        if self.muted.is_set():
            return
        mono = indata[:, 0].copy()
        if self._native_rate != SAMPLE_RATE:
            f = mono.astype(np.float32)
            n = int(len(f) * SAMPLE_RATE / self._native_rate)
            f = np.interp(np.linspace(0, len(f), n, endpoint=False), np.arange(len(f)), f)
            self._resample_buf = np.concatenate([self._resample_buf, f])
            while len(self._resample_buf) >= FRAME_SAMPLES:
                chunk, self._resample_buf = self._resample_buf[:FRAME_SAMPLES], self._resample_buf[FRAME_SAMPLES:]
                self._put(chunk.astype(np.int16))
            return
        self._put(mono)

    def _put(self, frame: np.ndarray) -> None:
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.frames.get_nowait()
            with contextlib.suppress(queue.Full):
                self.frames.put_nowait(frame)

    def drain(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    async def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return await asyncio.to_thread(self.frames.get, True, timeout)
        except queue.Empty:
            return None

    @property
    def alive(self) -> bool:
        return self._stream is not None and bool(self._stream.active)

    def stop(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None


class Player:
    """Interruptible playback of PCM (sounddevice) or encoded audio (MCI)."""

    def __init__(self, device: int | None) -> None:
        self.device = device
        self._stop = threading.Event()
        self.playing = threading.Event()
        self._mci_alias = f"scar_tts_{os.getpid()}"

    def stop(self) -> None:
        self._stop.set()
        if sys.platform == "win32":
            self._mci(f"stop {self._mci_alias}")
            self._mci(f"close {self._mci_alias}")
        with contextlib.suppress(Exception):
            import sounddevice as sd

            sd.stop()

    @staticmethod
    def _mci(cmd: str) -> str:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
        return buf.value

    def play_pcm_sync(self, pcm16: bytes, rate: int) -> bool:
        """Blocks until done; returns False if interrupted."""
        import sounddevice as sd

        self._stop.clear()
        self.playing.set()
        try:
            audio = np.frombuffer(pcm16, dtype=np.int16)
            sd.play(audio, rate, device=self.device)
            duration = len(audio) / rate
            end = time.monotonic() + duration + 0.3
            while time.monotonic() < end:
                if self._stop.wait(0.05):
                    sd.stop()
                    return False
            sd.wait()
            return True
        finally:
            self.playing.clear()

    def play_encoded_sync(self, data: bytes, ext: str) -> bool:
        if sys.platform != "win32":
            raise RuntimeError("encoded playback needs Windows MCI")
        self._stop.clear()
        self.playing.set()
        fd, path = tempfile.mkstemp(suffix=f".{ext}", prefix="scar_tts_")
        os.close(fd)
        try:
            Path(path).write_bytes(data)
            self._mci(f'open "{path}" type mpegvideo alias {self._mci_alias}')
            self._mci(f"play {self._mci_alias}")
            time.sleep(0.1)
            while self._mci(f"status {self._mci_alias} mode") == "playing":
                if self._stop.wait(0.05):
                    self._mci(f"stop {self._mci_alias}")
                    return False
            return True
        finally:
            self._mci(f"close {self._mci_alias}")
            self.playing.clear()
            Path(path).unlink(missing_ok=True)


def rms_level(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((frame.astype(np.float32) / 32768.0) ** 2)))
