import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.audio.aec import BLOCK, ReferenceBuffer  # noqa: E402

x24 = np.sin(np.arange(24000 * 2) * 2 * np.pi * 440 / 24000).astype(np.float32)
x16 = np.interp(np.arange(0, len(x24), 1.5), np.arange(len(x24)), x24).astype(np.float32)
rb = ReferenceBuffer(24000)
got = []
for i in range(0, len(x16) - BLOCK, BLOCK):
    rb.push(x24[int(i * 1.5): int((i + BLOCK) * 1.5)])
    got.append(rb.take(BLOCK))
got = np.concatenate(got)
print("pushed-per-block 16k samples:", [len(np.arange(0, 768, 1.5))], "total got", len(got), "vs", len(x16))
err = np.abs(got[:len(x16)] - x16[:len(got)])
print("max abs diff vs direct resample:", float(err.max()), "first bad index:", int(np.argmax(err > 1e-2)) if (err > 1e-2).any() else None)
