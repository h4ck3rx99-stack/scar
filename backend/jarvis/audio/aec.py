"""Acoustic echo cancellation: removes Scar's own voice (played on the speakers) from the microphone signal,
so "Scar, stop" works over its speech on laptop speakers.

Partitioned-block frequency-domain NLMS (PBFDAF): the reference is exactly what the speaker played; the filter
learns the speaker -> room -> mic path (delay + reverb, up to PARTITIONS x BLOCK samples = 512 ms at 16 kHz) and
subtracts its estimate. Adaptation pauses while the user talks over Scar (double-talk), so the filter isn't
trained on the user's voice.
"""
import collections
import threading

import numpy as np

BLOCK = 512
PARTITIONS = 16


class ReferenceBuffer:
    """What the speaker actually played, resampled to 16 kHz, consumed block by block by the AEC."""

    def __init__(self, out_sr=24000, max_s=2.0):
        self.ratio = 16000 / out_sr
        self.buf = collections.deque()
        self.n = 0
        self.max = int(16000 * max_s)
        self._lock = threading.Lock()
        self._phase = 0.0

    def push(self, samples_out_sr: np.ndarray):
        """Called from the speaker callback with every buffer it plays (including silence)."""
        n_out = len(samples_out_sr)
        idx = np.arange(self._phase, n_out, 1 / self.ratio)
        self._phase = idx[-1] + 1 / self.ratio - n_out if len(idx) else self._phase - n_out
        res = np.interp(idx, np.arange(n_out), samples_out_sr).astype(np.float32) if len(idx) else np.zeros(0, np.float32)
        with self._lock:
            self.buf.append(res)
            self.n += len(res)
            while self.n > self.max:
                self.n -= len(self.buf.popleft())

    def take(self, n=BLOCK) -> np.ndarray:
        """Next n reference samples. Keeps the backlog near one block so the reference never lags its echo."""
        with self._lock:
            while self.n - len(self.buf[0] if self.buf else []) >= 2 * n and self.buf:
                self.n -= len(self.buf.popleft())
            out = np.zeros(n, np.float32)
            filled = 0
            while filled < n and self.buf:
                c = self.buf[0]
                k = min(len(c), n - filled)
                out[filled:filled + k] = c[:k]
                filled += k
                if k == len(c):
                    self.buf.popleft()
                else:
                    self.buf[0] = c[k:]
                self.n -= k
            return out


class EchoCanceller:
    # On ~2 minutes of real TTS speech through a simulated room (bench/aec_long.py) these settings are stable and
    # reach ~17 dB after 10 s of Scar talking, ~30 dB after 15 s, ~38 dB steady. The learned echo path is saved to
    # disk, so after the first session it starts out converged.
    def __init__(self, block=BLOCK, partitions=PARTITIONS, mu=0.3, over=2.0):
        self.N, self.P, self.mu = block, partitions, mu
        self.over = over          # residual suppression strength (over-subtraction factor)
        self.norm = 8.0           # step normalization (higher = slower but safer on bursty speech)
        bins = block + 1
        self.X = np.zeros((partitions, bins), np.complex64)    # reference spectra, newest first
        self.W = np.zeros((partitions, bins), np.complex64)    # filter partitions
        self.Pxx = np.full(bins, 1e-6, np.float32)             # smoothed reference power per bin
        self.prev_ref = np.zeros(block, np.float32)
        self.erle_db = 0.0
        self.residual_ratio = 1.0
        self.resets = 0           # divergence resets (headphones give it nothing to learn)

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        N = self.N
        Xf = np.fft.rfft(np.concatenate([self.prev_ref, ref]))
        self.prev_ref = ref.astype(np.float32)
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = Xf
        y = np.fft.irfft((self.W * self.X).sum(axis=0))[N:]
        e = (mic - y).astype(np.float32)
        self.last_linear = e

        ref_pow = float(np.mean(ref ** 2))
        mic_pow = float(np.mean(mic ** 2))
        est_pow = float(np.mean(y ** 2))
        err_pow = float(np.mean(e ** 2))
        if ref_pow > 1e-7:
            self.erle_db = 0.9 * self.erle_db + 0.1 * 10 * np.log10((mic_pow + 1e-12) / (err_pow + 1e-12))
            # How much echo is still left relative to what we cancelled (drives the residual suppressor).
            self.residual_ratio = float(np.clip(10 ** (-self.erle_db / 10), 0.01, 1.0))
            if self.erle_db < -6:
                # Worse than doing nothing (headphones: no echo to learn, or a changed room): start over.
                self.W[:] = 0
                self.erle_db = 0.0
                self.resets += 1
        # Never hand the ears something louder than the raw mic: that's the filter adding noise, not removing echo.
        worse = err_pow > mic_pow * 1.05 + 1e-10
        if worse:
            self.last_linear = mic.astype(np.float32)
        # Adapt only when Scar is actually playing and the user isn't clearly talking over it (double-talk):
        # the residual being much louder than the echo estimate means there's near-end speech.
        converged = self.erle_db > 6
        double_talk = converged and err_pow > 4 * est_pow + 1e-7
        E = np.fft.rfft(np.concatenate([np.zeros(N, np.float32), e]))
        if ref_pow > 1e-7 and not double_talk:
            self.Pxx = 0.8 * self.Pxx + 0.2 * (np.abs(self.X) ** 2).sum(axis=0).astype(np.float32) / self.P
            # Regularize against quiet bins/pauses in speech, which would otherwise get huge steps and diverge.
            delta = max(1e-3, 0.1 * float(self.Pxx.mean()))
            G = self.mu * np.conj(self.X) * E / (self.Pxx * self.norm + delta)
            g = np.fft.irfft(G, axis=1)[:, :N]              # gradient constraint: keep the causal half
            self.W += np.fft.rfft(np.concatenate([g, np.zeros_like(g)], axis=1), axis=1).astype(np.complex64)
        if worse:
            return mic.astype(np.float32)
        return self._suppress(e, y) if ref_pow > 1e-7 else e

    # ------------------------------------------------------------ persistence
    def save(self, path):
        if self.erle_db > 15:   # only keep a filter that actually works
            np.savez(path, W=self.W, erle=self.erle_db)

    def load(self, path):
        try:
            d = np.load(path)
            if d["W"].shape == self.W.shape:
                self.W = d["W"].astype(np.complex64)
                self.erle_db = float(d["erle"]) * 0.5   # trust it, but let the double-talk detector re-earn it
        except (OSError, KeyError, ValueError):
            pass

    def _suppress(self, e, y):
        """Residual echo suppression: in each frequency band, shrink what still looks like Scar's voice.
        Wiener-style gain from the echo estimate vs. the residual; the user's voice (not in the estimate) passes."""
        # This audio only feeds VAD / wake word / speech-to-text (it is never played), so a per-block gain
        # without overlap-add is fine.
        Ef = np.fft.rfft(e)
        echo_p = self.over * np.abs(np.fft.rfft(y)) ** 2 * self.residual_ratio
        gain = np.clip(1 - echo_p / (np.abs(Ef) ** 2 + 1e-9), 0.1, 1.0)
        return np.fft.irfft(Ef * gain, n=len(e)).astype(np.float32)
