"""Open-vocabulary wake word for Scar's own name ("Scar", "Hey Scar", "OK Scar") with sherpa-onnx keyword spotting.

A small streaming zipformer (3.3M params, ~15 ms per block on the CPU) listens continuously; keywords are spelled
into its BPE tokens at start-up, so no training is needed and the name follows JARVIS_NAME. It runs alongside the
openWakeWord "Hey Jarvis" model and replaces the slower Whisper-based name spotting as the primary trigger.
"""
import logging
from pathlib import Path

import numpy as np

from jarvis import config

log = logging.getLogger("kws")
MODEL_DIR = config.MODELS_DIR / "kws" / "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"


def keyword_lines(name: str, score=1.5, threshold=0.25):
    """"scar" -> BPE token lines for the spotter ("▁S CAR :1.5 #0.25 @SCAR" style), with greeting variants."""
    import sentencepiece as spm
    sp = spm.SentencePieceProcessor(model_file=str(MODEL_DIR / "bpe.model"))
    name = name.upper()
    lines = []
    for phrase, boost, thr in ((name, score, threshold), (f"HEY {name}", score, threshold * 0.8),
                               (f"OK {name}", score, threshold * 0.8)):
        pieces = sp.encode(phrase, out_type=str)
        lines.append(f"{' '.join(pieces)} :{boost} #{thr:.2f} @{phrase.replace(' ', '_')}")
    return lines


class NameSpotter:
    def __init__(self, name=None, threshold=None):
        import sherpa_onnx
        name = name or config.NAME
        thr = threshold if threshold is not None else config.KWS_THRESHOLD
        kw_file = config.APPDATA_DIR / "kws_keywords.txt"
        kw_file.write_text("\n".join(keyword_lines(name, threshold=thr)) + "\n", encoding="utf-8")
        f = lambda part: str(MODEL_DIR / f"{part}-epoch-12-avg-2-chunk-16-left-64.int8.onnx")  # noqa: E731
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(MODEL_DIR / "tokens.txt"), encoder=f("encoder"), decoder=f("decoder"), joiner=f("joiner"),
            num_threads=1, keywords_file=str(kw_file), provider="cpu", max_active_paths=4,
            keywords_score=1.5, keywords_threshold=thr, num_trailing_blanks=1)
        self.stream = self.spotter.create_stream()
        self.last = ""
        log.info("name wake word ready: %s", ", ".join(l.split("@")[1] for l in keyword_lines(name)))

    def feed(self, block: np.ndarray) -> str:
        """16 kHz float block -> the keyword heard ("SCAR", "HEY_SCAR"...) or ""."""
        self.stream.accept_waveform(config.SAMPLE_RATE, block)
        hit = ""
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            r = self.spotter.get_result(self.stream)
            if r:
                hit = r
                self.spotter.reset_stream(self.stream)
        if hit:
            self.last = hit
        return hit

    def reset(self):
        self.stream = self.spotter.create_stream()


def available() -> bool:
    return (MODEL_DIR / "tokens.txt").exists()
