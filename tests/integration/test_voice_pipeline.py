"""Voice pipeline without speakers or a microphone: Windows SAPI synthesises speech to a WAV in memory, Silero VAD
endpoints it frame by frame exactly as the live loop does, and faster-whisper transcribes the utterance.
Nothing is played or recorded, so this runs silently in the normal suite."""

from __future__ import annotations

import os
import re

import numpy as np
import pytest

from scar.config import paths
from scar.voice.audio_io import FRAME_SAMPLES, SAMPLE_RATE
from scar.voice.vad import Endpointer, SileroVad, make_vad

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows SAPI")

PHRASE = "open the downloads folder"


def _resample(pcm: bytes, rate: int) -> np.ndarray:
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(audio) * SAMPLE_RATE / rate)
    return np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.int16)


@pytest.fixture(scope="module")
def spoken() -> np.ndarray:
    from scar.providers.tts.service import SapiTts

    if not SapiTts.available():
        pytest.skip("SAPI unavailable")
    audio = SapiTts().synth_sync(PHRASE)
    assert audio.sample_rate > 0 and len(audio.pcm16) > 8000
    silence = np.zeros(SAMPLE_RATE, dtype=np.int16)
    return np.concatenate([silence, _resample(audio.pcm16, audio.sample_rate), silence])


def test_silero_vad_is_used_and_separates_speech_from_silence(spoken: np.ndarray) -> None:
    vad = make_vad()
    assert isinstance(vad, SileroVad), "Silero VAD should load (it ships with faster-whisper)"
    frames = [spoken[i : i + FRAME_SAMPLES] for i in range(0, len(spoken) - FRAME_SAMPLES, FRAME_SAMPLES)]
    probs = [vad.prob(f) for f in frames]
    lead = probs[: int(0.8 * SAMPLE_RATE / FRAME_SAMPLES)]
    assert max(lead) < 0.5, "leading silence is not speech"
    assert max(probs) > 0.8, "synthesised speech is detected"


def test_endpointer_yields_one_utterance_that_whisper_transcribes(spoken: np.ndarray) -> None:
    ep = Endpointer(make_vad())
    utterances = []
    for i in range(0, len(spoken) - FRAME_SAMPLES, FRAME_SAMPLES):
        out = ep.feed(spoken[i : i + FRAME_SAMPLES])
        if out is not None:
            utterances.append(out)
    assert len(utterances) == 1
    pcm = utterances[0]
    assert 0.8 < len(pcm) / 2 / SAMPLE_RATE < 5.0
    whisper_dir = paths.default_data_dir() / "models" / "whisper"
    if not whisper_dir.exists():
        pytest.skip("faster-whisper model not downloaded yet (first voice use downloads it)")
    from scar.providers.stt.service import LocalWhisper

    text = LocalWhisper("base", str(whisper_dir), None).transcribe_sync(pcm, SAMPLE_RATE, "en").text
    words = re.sub(r"[^a-z ]", "", text.lower()).split()
    assert {"open", "downloads", "folder"} <= set(words), text


class _FakeMic:
    def __init__(self) -> None:
        import threading

        self.muted = threading.Event()
        self.drained = 0
        self.alive = True

    def stop(self) -> None:
        self.alive = False

    def start(self) -> None:
        self.alive = True

    def drain(self) -> None:
        self.drained += 1


class _FakePlayer:
    def __init__(self) -> None:
        import threading

        self.played: list[int] = []
        self.playing = threading.Event()
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1

    def play_pcm_sync(self, pcm: bytes, rate: int) -> bool:
        self.played.append(len(pcm))
        return True

    def play_encoded_sync(self, data: bytes, encoding: str) -> bool:
        self.played.append(len(data))
        return True


class _FakeTts:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str):  # type: ignore[no-untyped-def]
        from scar.providers.base import SpeechAudio

        self.spoken.append(text)
        return SpeechAudio(pcm16=b"\x00\x00" * 1600, sample_rate=16000, provider="fake")


async def test_voice_utterance_runs_a_task_and_speaks_the_real_result(runtime_parts) -> None:
    """A spoken request goes through the same task runner and pipeline as typed text, and the spoken reply is the
    task's actual result (with the mic muted while speaking)."""
    from scar.agent.runner import TaskManager
    from scar.voice.session import VoiceSession

    services = runtime_parts["services"]
    services.tasks = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    services.tts = _FakeTts()
    vs = VoiceSession(services)
    vs.mic, vs.player = _FakeMic(), _FakePlayer()  # type: ignore[assignment]
    await vs.handle_utterance("remember that my voice test colour is teal")
    assert services.tts.spoken and "remembered" in services.tts.spoken[-1].lower()
    assert vs.player.played and vs.mic.drained == 1 and not vs.mic.muted.is_set()  # type: ignore[union-attr]
    assert any("teal" in m.text for m in services.memory.list())
    rows = services.db.query("SELECT origin FROM tasks ORDER BY created_at DESC LIMIT 1")
    assert rows and rows[0]["origin"] == "voice"


async def test_voice_stop_command_cancels_and_confirms(runtime_parts) -> None:
    from scar.agent.runner import TaskManager
    from scar.voice.session import VoiceSession

    services = runtime_parts["services"]
    services.tasks = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    services.tts = _FakeTts()
    vs = VoiceSession(services)
    vs.mic, vs.player = _FakeMic(), _FakePlayer()  # type: ignore[assignment]
    await vs.handle_utterance("stop")
    assert services.tts.spoken == ["Okay."]


async def test_voice_states_and_privacy_mute_are_published(runtime_parts) -> None:
    """The app's voice indicator follows real session state; mute stops the capture stream, not just processing."""
    from scar.agent.runner import TaskManager
    from scar.voice.session import VoiceSession

    services = runtime_parts["services"]
    services.tasks = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    services.tts = _FakeTts()
    seen: list = []
    services.bus.add_listener(lambda e: seen.append(e) if e.kind == "voice_state" else None)
    vs = VoiceSession(services)
    vs.active = True
    vs.mic, vs.player = _FakeMic(), _FakePlayer()  # type: ignore[assignment]
    await vs.handle_utterance("remember that the voice state test ran")
    phases = [e.state for e in seen]
    assert "thinking" in phases and "speaking" in phases and phases[-1] == "idle"
    vs.set_muted(True)
    assert vs.mic.alive is False and seen[-1].muted is True and seen[-1].mic_active is False  # type: ignore[union-attr]
    vs.set_muted(False)
    assert vs.mic.alive is True and seen[-1].mic_active is True  # type: ignore[union-attr]
