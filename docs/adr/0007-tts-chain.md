# ADR 0007: Text-to-speech chain: Azure (key) → edge-tts (unofficial) → Windows SAPI → Kokoro (optional extra)

Status: accepted (2026-09-26)

## Decision

Windows SAPI is built in, offline and license-free, so it is the dependable local fallback. edge-tts needs no key but is an unofficial interface that may break without notice (documented). Kokoro-onnx is an optional extra because its phonemizer pulls GPL-3.0 components; Piper is GPL-3.0 and was not added to the core.

## Alternatives and rationale

Keeps the core free of GPL code (directive A5.4).
