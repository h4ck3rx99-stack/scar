"""Mic input and speaker output streams (sounddevice / PortAudio)."""
import asyncio
import collections
import logging
import threading

import numpy as np
import sounddevice as sd

from jarvis import config

log = logging.getLogger("audio")


def resolve_device(spec, kind):
    """spec: None (system default), an index, or a name substring like 'Virtual Audio Cable'."""
    if spec is None:
        return None
    if str(spec).isdigit():
        return int(spec)
    want_in = kind == "input"
    for i, d in enumerate(sd.query_devices()):
        ch = d["max_input_channels"] if want_in else d["max_output_channels"]
        # MME (hostapi 0) resamples for us, so 16 kHz / 24 kHz streams always open.
        if ch > 0 and d["hostapi"] == 0 and str(spec).lower() in d["name"].lower():
            return i
    raise ValueError(f"no {kind} device matching {spec!r}")


class Microphone:
    """Pushes 512-sample float32 blocks into an asyncio.Queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.device = resolve_device(config.INPUT_DEVICE, "input")
        self.stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=config.BLOCK, device=self.device, callback=self._cb,
        )

    def _cb(self, indata, frames, t, status):
        block = indata[:, 0].copy()
        self.loop.call_soon_threadsafe(self._put, block)

    def _put(self, block):
        if self.queue.full():  # never let a stalled consumer build up latency
            self.queue.get_nowait()
        self.queue.put_nowait(block)

    def start(self):
        self.stream.start()
        name = sd.query_devices(self.device, "input")["name"]
        log.info("mic: %s", name)

    def stop(self):
        self.stream.stop()
        self.stream.close()


class Speaker:
    """Gapless queued playback. Knows when audio has truly finished playing (not just been queued)
    and reports loudness for the avatar's visor animation."""

    SR = 24000

    def __init__(self, loop: asyncio.AbstractEventLoop, on_level=None, reference=None):
        self.loop = loop
        self.on_level = on_level
        self.reference = reference   # aec.ReferenceBuffer: gets exactly what is played, for echo cancellation
        self.gain = 1.0              # Scar's own voice volume ("speak louder"), separate from the Windows volume
        self._chunks = collections.deque()
        self._lock = threading.Lock()
        self._idle = asyncio.Event()
        self._idle.set()
        self._level_acc = []
        self.device = resolve_device(config.OUTPUT_DEVICE, "output")
        self.stream = sd.OutputStream(
            samplerate=self.SR, channels=1, dtype="float32", blocksize=1200,  # 50 ms
            device=self.device, callback=self._cb, latency="low",
        )
        self._draining = 0  # callbacks of silence to wait so the device buffer really empties

    def start(self):
        self.stream.start()

    def _cb(self, outdata, frames, t, status):
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                c = self._chunks[0]
                n = min(len(c), frames - filled)
                out[filled:filled + n] = c[:n]
                filled += n
                if n == len(c):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = c[n:]
            empty = not self._chunks
        out[filled:] = 0
        if self.reference is not None:
            self.reference.push(out.copy())
        if filled:
            self._draining = 3
            if self.on_level:
                rms = float(np.sqrt(np.mean(out[:filled] ** 2)))
                self.loop.call_soon_threadsafe(self.on_level, min(1.0, rms * 4))
        elif empty and self._draining:
            self._draining -= 1
            if self._draining == 0:
                self.loop.call_soon_threadsafe(self._idle.set)
                if self.on_level:
                    self.loop.call_soon_threadsafe(self.on_level, 0.0)

    def play(self, samples: np.ndarray, sr: int):
        if sr != self.SR:
            idx = np.arange(0, len(samples), sr / self.SR)
            samples = np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)
        if not len(samples):
            return
        if self.gain != 1.0:
            samples = np.clip(samples * self.gain, -1.0, 1.0).astype(np.float32)
        self._idle.clear()
        with self._lock:
            self._chunks.append(samples)
            self._draining = 3

    def stop(self):
        """Barge-in: drop everything queued."""
        with self._lock:
            self._chunks.clear()

    @property
    def busy(self):
        return not self._idle.is_set()

    async def wait_idle(self):
        await self._idle.wait()
