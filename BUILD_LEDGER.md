# SCAR Build Ledger

Read this first when resuming. It tracks the Definition of Done, current work, evidence and blockers.

## Definition of Done (Part F1)

- [x] Every subsystem in Part C is implemented and integrated, with no stubs in runtime code. Evidence:
  `tests/security/test_secrets_and_isolation.py::test_no_stub_markers_in_runtime`; 114 tools registered.
- [x] `uv sync` works; `uv run scar`, `--voice`, `doctor`, `status`, `config`, `logs` work. Evidence: REPL piped run;
  one-shot via the managed llama-server; the voice loopback; `tests/integration/test_lifecycle_browser_dev.py::test_cli_commands`.
- [x] Permission engine, taint, scope anchoring, path guard, command guard, redaction, kill switch pass security tests. Evidence: `tests/security` (≈150 tests incl. hypothesis property tests and the adversarial injection corpus).
- [x] Provider router: fallback across cloud candidates (scripted, every error branch), local fallback (live: Ollama and the managed llama.cpp), degraded mode. Cloud candidates are not live-verified: no keys on this machine.
- [x] Local model lifecycle: start on demand (live, `-ngl 99`), idle unload (test with a fake server, plus live measurement), reuse without kill (test), admission (tests).
- [x] All C9 tool domains are implemented with postconditions (docs/tools.md catalog).
- [x] Voice pipeline: loopback verified (SAPI → VAC → Silero VAD → faster-whisper, exact transcript); barge-in and voice approval logic (policy tests); STT/TTS fallback chains.
- [x] Memory, scheduler, monitors, notifications across a daemon restart: `scripts/acceptance/daemon_restart_check.py` VERIFIED (alias kept, missed reminder reported once, interrupted monitor surfaced).
- [x] Full test suite passes; skips are only the marked live tests.
- [x] Acceptance D3.1–D3.12 executed in one clean full run (2026-09-27): 1–6 and 9–12 VERIFIED; 7, 8 IMPLEMENTED-UNVERIFIED (no comms credentials). Evidence: docs/ACCEPTANCE_REPORT.md.
- [x] Resource and security acceptance D4/D5: D4 measured (idle CPU 0.002 %, RSS 75 MiB, VRAM released after idle, no orphans); D5 via security tests.
- [x] Review passes D6: security (threat model, pipeline walk, pip-audit clean), code quality (ruff clean, pyright strict 0 errors), reliability (failure-injection tests), provider fallback (unit tests), permissions (policy tests). Resource profiling done (D4).
- [x] All documentation in Part E complete, including docs/ACCEPTANCE_REPORT.md and the README capability table.
- [x] No secrets committed; clean final commit.

## Current work item

Done. All DoD items are met; remaining gaps are external prerequisites (see Blockers).

## Findings fixed during verification

- Local file contents (untrusted) were not tracked as private → exfiltration via URL to an unnamed domain. Fixed + test.
- Paraphrased memory write after injected content was allowed by grant. Fixed (non-grantable ASK) + test.
- Privacy overrides unparseable from env; SettingsError leaked. Fixed + test.
- A tool exception type outside the caught list crashed the whole task. Pipeline now contains every tool error.
- A plain-text final answer after a failed action was reported as success. Fixed.
- CLI objective argument swallowed subcommands. Fixed (explicit routing) + test.
- fs.search silently stopped at a model-chosen max_depth → now reports unsearched folders.
- 8k local context overflowed with tool schemas → 16k with flash-attn and a q8 KV cache in one slot, local tool cap, overflow retry with trimmed tools, honest "too large" message.
- Fast-path grammar grabbed multi-clause objectives → compound objectives go to the planner.
- Grounding stripped "press" from the label "Press Me" → both variants are tried.
- Local tool cap kept the first-registered tools, not relevant ones → relevance-ranked selection.
- Research via a small model skipped fetching → deterministic `web.research` tool; citation check on text documents written via fs.write.
- Browser crash lost tab URLs → URLs tracked per tab.
- Monitors were marked cancelled on daemon shutdown and never re-armed → shutdown no longer cancels them.
- Model-supplied dev-server hints (a stale URL from an earlier task, a guessed ready regex) replaced auto-detection, so a
  running server was never reported ready → hints now widen detection; the server's printed URLs are probed first.
- After the vision model ran, the LLM was started with only 2 GPU layers (VRAM held by the idle VLM) and timed out →
  SCAR evicts its own idle local model (never one serving a request) before starting another.
- Process crash (access violation): PyWinRT bundles msvcp140.dll 14.29; once loaded (OCR), a later onnxruntime import
  (memory embeddings) killed the process → `scar/__init__.py` pins the system msvcp140.dll (14.50) at import;
  subprocess regression test fails without it.
- Task-history memory embedding ran on the event loop thread → moved to a worker thread.

## Blockers (external)

- No cloud provider keys → cloud LLM/vision/STT/search adapters are IMPLEMENTED-UNVERIFIED (request/response/error handling tested with HTTP mocks).
- No email/messaging/calendar credentials → comms send paths are IMPLEMENTED-UNVERIFIED (65 fixture tests).
- `gh` not installed; GitHub via REST needs `GITHUB_TOKEN`.

## Decisions

See `docs/adr/` (0001–0013).
