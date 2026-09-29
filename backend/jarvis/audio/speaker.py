"""Speaker verification: does this utterance sound like the enrolled user (or like Scar's own voice)?

Uses a CAM++ speaker-embedding model (VoxCeleb) through sherpa-onnx, on the CPU (~20-40 ms per utterance).
The user's voiceprint is the normalized mean of a few enrollment utterances, saved in %APPDATA%\\jarvis\\voiceprint.npy.
"""
import logging
import threading

import numpy as np

from jarvis import config

log = logging.getLogger("speaker")
MODEL = config.MODELS_DIR / "speaker" / "campplus_en_voxceleb.onnx"
PRINT_FILE = config.APPDATA_DIR / "voiceprint.npy"


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


class VoicePrint:
    def __init__(self):
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2, provider="cpu")
        self.ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self._lock = threading.Lock()
        self.user = _unit(np.load(PRINT_FILE)) if PRINT_FILE.exists() else None
        self.self_print = None
        self.samples = []
        self.threshold = config.VOICE_MATCH

    @property
    def enrolled(self):
        return self.user is not None

    def embed(self, audio16k: np.ndarray):
        if len(audio16k) < 16000 * 0.6:   # too short to say anything about the speaker
            return None
        with self._lock:
            s = self.ext.create_stream()
            s.accept_waveform(16000, audio16k.astype(np.float32))
            s.input_finished()
            return _unit(self.ext.compute(s))

    def similarity(self, audio16k, who="user"):
        ref = self.user if who == "user" else self.self_print
        if ref is None:
            return None
        e = self.embed(audio16k)
        return None if e is None else float(np.dot(ref, e))

    def is_user(self, audio16k):
        """True/False when enrolled and the clip is long enough; None when we can't tell (then don't filter)."""
        sim = self.similarity(audio16k, "user")
        if sim is None:
            return None
        log.info("voice match %.2f (threshold %.2f)", sim, self.threshold)
        return sim >= self.threshold

    def is_self(self, audio16k):
        """Is this Scar's own voice coming back through the mic?"""
        sim = self.similarity(audio16k, "self")
        return sim is not None and sim >= 0.55 and (self.user is None or sim > float(np.dot(self.user, self.embed(audio16k))))

    def learn_self(self, tts):
        """Voiceprint of Scar's current TTS voice, from a couple of synthesized sentences."""
        clips = []
        for text in ("I'm Scar, your desktop assistant. Opening that for you now.",
                     "The weather today is mostly clear with a light breeze in the evening."):
            a, sr = tts.synth(text)
            clips.append(np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32))
        embs = [e for e in (self.embed(c) for c in clips) if e is not None]
        if embs:
            self.self_print = _unit(np.mean(embs, axis=0))

    # ------------------------------------------------------------ enrollment
    def add_sample(self, audio16k):
        e = self.embed(audio16k)
        if e is not None:
            self.samples.append(e)
        return e is not None

    def finish_enrollment(self):
        if len(self.samples) < 2:
            self.samples = []
            return False
        self.user = _unit(np.mean(self.samples, axis=0))
        config.APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        np.save(PRINT_FILE, self.user)
        self.samples = []
        return True

    def forget(self):
        self.user = None
        PRINT_FILE.unlink(missing_ok=True)
