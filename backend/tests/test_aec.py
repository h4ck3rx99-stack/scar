import numpy as np

from jarvis.audio.aec import BLOCK, EchoCanceller, ReferenceBuffer


def _room(n, seed=0):
    rng = np.random.default_rng(seed)
    ref = (rng.standard_normal(n) * (0.3 + np.sin(np.arange(n) / 2400) ** 2)).astype(np.float32) * 0.1
    rir = rng.standard_normal(2400) * np.exp(-np.arange(2400) / 700.0)
    rir[0] += 3.0
    rir = np.concatenate([np.zeros(2400), rir]) * 0.08          # 150 ms delay + reverb
    return ref, np.convolve(ref, rir)[:n].astype(np.float32)


def test_echo_is_removed_and_stays_stable():
    ref, mic = _room(16000 * 20)
    aec = EchoCanceller()
    lin = np.concatenate([(aec.process(mic[i:i + BLOCK], ref[i:i + BLOCK]), aec.last_linear)[1]
                          for i in range(0, len(mic) - BLOCK, BLOCK)])
    last = slice(len(lin) - 16000 * 4, len(lin))
    erle = 10 * np.log10(np.mean(mic[last] ** 2) / np.mean(lin[last] ** 2))
    assert erle > 20, erle


def test_near_end_speech_passes_through():
    ref, mic = _room(16000 * 20)
    aec = EchoCanceller()
    for i in range(0, len(mic) - BLOCK, BLOCK):
        aec.process(mic[i:i + BLOCK], ref[i:i + BLOCK])
    # Now the user talks with Scar silent: the output must be (almost) the user's signal.
    user = (np.sin(np.arange(BLOCK * 20) * 2 * np.pi * 220 / 16000) * 0.2).astype(np.float32)
    out = np.concatenate([aec.process(user[i:i + BLOCK], np.zeros(BLOCK, np.float32)) for i in range(0, len(user), BLOCK)])
    assert np.mean(out[BLOCK * 10:] ** 2) > 0.8 * np.mean(user[BLOCK * 10:] ** 2)


def test_reference_buffer_resamples_exactly():
    x24 = np.sin(np.arange(24000) * 2 * np.pi * 440 / 24000).astype(np.float32)
    rb = ReferenceBuffer(24000)
    got = []
    for i in range(0, 15000, BLOCK):
        rb.push(x24[int(i * 1.5): int((i + BLOCK) * 1.5)])
        got.append(rb.take(BLOCK))
    x16 = np.interp(np.arange(0, len(x24), 1.5), np.arange(len(x24)), x24)
    got = np.concatenate(got)
    assert np.max(np.abs(got - x16[: len(got)])) < 1e-4


def test_headphones_never_make_the_mic_louder():
    """Headset: Scar's voice never reaches the mic. The canceller must not add its own junk to faint room noise."""
    rng = np.random.default_rng(1)
    aec = EchoCanceller()
    n = aec.N
    worst = 0.0
    for i in range(600):
        ref = (0.3 * np.sin(np.arange(n) * 0.05 + i) + 0.1 * rng.standard_normal(n)).astype(np.float32)
        mic = (1e-4 * rng.standard_normal(n)).astype(np.float32)
        out = aec.process(mic, ref)
        worst = max(worst, float(np.mean(out ** 2)) / float(np.mean(mic ** 2)))
    assert worst <= 1.06
    assert aec.erle_db > -6
