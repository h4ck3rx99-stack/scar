"""faster-whisper STT. Tries CUDA float16, falls back to CPU int8."""
import logging

import numpy as np

from jarvis import config
from jarvis.gpu import add_nvidia_dll_dirs

log = logging.getLogger("stt")

# App names the recogniser should spell correctly.
HOTWORDS = "Jarvis, Chrome, VS Code, Spotify, YouTube, Notepad, Discord, Edge, File Explorer, WhatsApp, Terminal"


class WhisperSTT:
    def __init__(self):
        add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        attempts = [("cuda", "float16"), ("cpu", "int8")]
        if config.WHISPER_DEVICE == "cpu":
            attempts = [("cpu", "int8")]
        last = None
        for device, ctype in attempts:
            try:
                self.model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=ctype)
                # A real decode proves the kernels work on this GPU, not just that the DLLs loaded.
                self._run(np.zeros(16000, dtype=np.float32))
                self.device = f"{device}/{ctype}"
                log.info("whisper on %s", self.device)
                return
            except Exception as e:  # noqa: BLE001 - any failure means try the next device
                last = e
                log.warning("whisper %s failed: %s", device, e)
        raise RuntimeError(f"no usable whisper backend: {last}")

    def _run(self, audio):
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=HOTWORDS,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        return self._run(audio.astype(np.float32))
