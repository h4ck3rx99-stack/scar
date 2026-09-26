# Voice

```
uv run scar --voice          # voice session + text prompt at the same time
scar voice devices           # list microphones and speakers
scar voice test              # record 4 s → transcribe → speak it back
```

## Pipeline

microphone (16 kHz mono, 32 ms frames, bounded queue) → **VAD** (Silero v6 ONNX, bundled with faster-whisper;
energy fallback) → trigger (**push-to-talk** hotkey, or **wake word** with openWakeWord) → endpointing
(0.8 s trailing silence, `SCAR_MAX_UTTERANCE_SECONDS` cap) → **STT** (Groq Whisper if `GROQ_API_KEY`, else local
faster-whisper int8 on CPU) → the agent (same tools and permissions as text) → short spoken result through **TTS**
(Azure → edge-tts → Windows SAPI → Kokoro → text only).

Verified on the build machine with a Virtual Audio Cable loopback: SAPI speech played into the cable was captured,
endpointed by Silero VAD (4.0 s utterance) and transcribed exactly by local faster-whisper.

## Modes

| Mode | How | Setting |
|---|---|---|
| Push-to-talk (default) | press `SCAR_PTT_HOTKEY` (default Ctrl+Alt+Space), then speak | — |
| Wake word | say the wake word, then speak | `SCAR_VOICE_ENABLED=true`, `SCAR_WAKE_WORD` (hey_jarvis, alexa, hey_mycroft, hey_rhasspy), `SCAR_WAKE_WORD_THRESHOLD` |
| Conversation | after a reply, SCAR keeps listening for `SCAR_VOICE_SESSION_TIMEOUT` seconds without a trigger | — |

Wake-word models (~2 MB each) download on first use into `<data>/models/openwakeword`. Nothing is transcribed
while voice mode is inactive, and models unload after `SCAR_MODEL_IDLE_TIMEOUT`.

## Devices

`SCAR_MIC_DEVICE` and `SCAR_SPEAKER_DEVICE` accept an index or a name substring from `scar voice devices`. Devices
that can't open at 16 kHz are captured natively and resampled. If the microphone disconnects, SCAR says so and
continues with text. If TTS fails, replies are shown as text.

## Barge-in and echo

Voice is half-duplex with an echo guard: the microphone is muted while SCAR speaks, and drained afterwards, so SCAR
never hears (or approves) itself. Press the push-to-talk hotkey to interrupt speech (barge-in). "Stop" or "cancel"
cancels the running task. The kill-switch hotkey also stops speech.

## What SCAR speaks

Only short results and milestones. Long content is summarised to its first sentence plus "I've put the details on
screen."; links are read as "a link".

## Voice approvals

See [security.md](security.md#voice-approval). Summary:

* the approval is read back and followed by a short listening window
* "allow / yes / go ahead / send it" allows once
* "for this task" or "for this session" give scoped approval
* "always allow" requires a spoken "yes" to confirm
* "no / deny / cancel" denies; "never" denies always
* CRITICAL actions must be confirmed at the keyboard
* "revoke all my permissions" (after confirmation) removes saved grants
