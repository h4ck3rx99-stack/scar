# SCAR Final Build Ledger (desktop app + production finish)

Read this first when resuming. Continue from "Current item". Never start over.

## Definition of Done (Part 10)

- [ ] Desktop app (Quick Bar, main window, approvals, status, voice, control indicator, tasks, permissions, memory,
      settings, onboarding, diagnostics) fully wired to the real runtime
- [ ] App API versioned, typed, secured; contract + security tests pass
- [ ] UI security (Part 4) implemented and tested
- [ ] Latency targets met or measured with the gap explained; before/after recorded
- [ ] Cloud provider(s) + full fallback chain verified live
- [ ] Voice verified live with a real microphone
- [ ] Full 12-test acceptance suite re-run live, results recorded
- [ ] Game/full-screen awareness and default-browser handling work
- [ ] Installer builds, installs, runs, uninstalls cleanly on a clean profile
- [ ] All tests pass (pytest, frontend, API, UI e2e, accessibility); lint/type checks clean
- [ ] Visual review done (light/dark, 100%/150%), issues fixed
- [ ] CLI still works exactly as documented
- [ ] Documentation matches reality
- [ ] No secrets committed; repo clean; work committed

## Current item

Part 1 done → Part 2 (app API in the runtime).

## Baseline (2026-09-27, before this build)

Environment: Windows 11 **Home** (CoreSingleLanguage, build 26200) → Windows Sandbox unavailable; default browser
**Opera GX** (ProgId `Opera GXStable`); WebView2 153.0.4234.48; Node 24.14.1, npm 11.11, pnpm 11.7; Rust 1.96
(stable-x86_64-pc-windows-msvc); VS 2022 Community with VC tools; winget 1.29.

Groq key stored by the user (Credential Manager) — `scar providers test groq` → ✓ openai/gpt-oss-120b 433 ms.

Tests: 340 passed, 6 skipped (live), ruff clean, pyright strict 0.

Latency (`scripts/bench/latency.py`, end-to-end one-shot CLI, median of 3, Groq configured):

| Request | Median | Note |
|---|---|---|
| fast path "what's using my RAM" | 4.26 s | includes ~2 s runtime start per one-shot + process sampling |
| fast path "remember …" | 2.26 s | ≈ runtime start cost |
| simple question (cloud) | 3.31 s | Groq; gpt-oss-120b hit a TPM rate limit, fell back to qwen3.8-27b |
| simple question (local 8B, audit) | 25–35 s | no cloud key at the time |
| find-and-fix (local 8B, audit) | 215 s | |
| `import scar.cli.app` | 0.39 s | |

Streaming: **none** (no provider streams; replies arrive whole).

Finding during baseline: the audit's state-question guard (F-16) matched definitions ("what is RAM?") and answered
with the user's RAM usage → fixed (requires a possessive/now cue) with 11 parametrised cases.

## Decisions

(ADRs in docs/adr/, 0014+)

## Blockers

- Windows Sandbox: not available on Home → clean-install test uses a separate profile/data dir.
