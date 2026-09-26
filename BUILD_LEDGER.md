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

Resume here: voice subsystem (src/scar/voice/: audio_io.list_devices, vad, wakeword, ptt, session with barge-in + voice approval, cli.run_voice / voice_selftest — referenced lazily by cli/app.py, NOT yet written). Then: end-to-end LLM-path test via Ollama, unit/security test suites (D2/D5, injection corpus), acceptance harness D3.1-12, resource measurements D4, review passes D6, docs (Part E: README, architecture, security, providers w/ 2026-09-26 findings, tools, memory, voice, browser, configuration, development, testing, troubleshooting, ADRs), .env.example.

## Completed (with evidence)

- Reconnaissance → `docs/ENVIRONMENT.md`.
- `pyproject.toml` + `uv sync` resolved on Windows/Py3.11 (all deps installed, incl. winrt OCR, faiss-cpu 1.15.1, fastembed 0.8.1, faster-whisper 1.2.1, openwakeword 0.6.0).

- Core/config/storage/security (path guard, PS-AST command guard, taint, scope, policy, grants, approvals, audit, privacy, kill switch) — smoke-tested.
- Provider router + adapters (OpenAI-compat, Gemini, Anthropic, Ollama native), health/ratelimit, local lifecycle; live: router fell back to Ollama qwen3:8b with a correct tool call; STT/TTS (edge-tts, SAPI live), search (ddgs live), embeddings (fastembed live).
- 113 tools registered; runtime boots; 86 tests pass (fs pipeline 12, terminal 9, comms 65 by subagent).
- CLI (typer app, REPL, render, sessions embedded/daemon, doctor, status), IPC + daemon written; NOT yet exercised end-to-end.

## Provider research (2026-09-26)
Groq free: openai/gpt-oss-120b, gpt-oss-20b, qwen/qwen3.8-27b, whisper-large-v3(-turbo). Gemini: 3.x flash models; unpaid tier data may be used to improve Google products. Cerebras: trial credits w/ card. OpenRouter :free 20 RPM/50 per day. GitHub Models retired 2026-07-30 (excluded). Mistral Experiment free tier. Tavily 1000 credits/mo; Brave $5/mo credit (card).

## Blockers (external)

- No cloud provider API keys present → cloud LLM/vision/STT/search adapters will be IMPLEMENTED-UNVERIFIED.
- No messaging/email/calendar credentials → comms integrations IMPLEMENTED-UNVERIFIED.
- `gh` CLI not installed; GitHub via REST needs `GITHUB_TOKEN`.

## Decisions

See `docs/adr/`.
