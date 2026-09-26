# SCAR Build Ledger

Read this first when resuming. It tracks the Definition of Done, current work, evidence, and blockers.

## Definition of Done (Part F1)

- [ ] Every subsystem in Part C is implemented and integrated, with no stubs or placeholders in runtime code.
- [ ] `uv sync` from a clean clone succeeds; `uv run scar`, `--voice`, `doctor`, `status`, `config`, `logs` work.
- [ ] Permission engine, taint, scope anchoring, path guard, command guard, redaction, kill switch pass security tests.
- [ ] Provider router: fallback across ≥2 cloud candidates (where creds exist), local fallback, degraded mode, all tested.
- [ ] Local model lifecycle verified: start on demand, idle unload, reuse existing server, admission.
- [ ] All tool domains in C9 implemented with verification postconditions.
- [ ] Voice pipeline works with provider fallback, barge-in, voice approval.
- [ ] Memory, scheduler, monitors, notifications work across daemon restart.
- [ ] Full test suite passes; skips only marked/justified.
- [ ] Acceptance tests D3.1–D3.12 executed and recorded honestly.
- [ ] Resource and security acceptance (D4, D5) measured and verified.
- [ ] Review passes (D6) completed, findings fixed.
- [ ] All documentation in Part E complete and accurate.
- [ ] No secrets committed; `.gitignore` correct; repo committed clean.

## Current work item

Phase 1: core, config, storage, observability, security.

## Completed (with evidence)

- Reconnaissance → `docs/ENVIRONMENT.md`.
- `pyproject.toml` + `uv sync` resolved on Windows/Py3.11 (all deps installed, incl. winrt OCR, faiss-cpu 1.15.1, fastembed 0.8.1, faster-whisper 1.2.1, openwakeword 0.6.0).

## Blockers (external)

- No cloud provider API keys present → cloud LLM/vision/STT/search adapters will be IMPLEMENTED-UNVERIFIED.
- No messaging/email/calendar credentials → comms integrations IMPLEMENTED-UNVERIFIED.
- `gh` CLI not installed; GitHub via REST needs `GITHUB_TOKEN`.

## Decisions

See `docs/adr/`.
