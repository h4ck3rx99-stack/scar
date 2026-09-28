# SCAR Final Build Ledger (desktop app + production finish)

Read this first when resuming. Continue from "Current item". Never start over.

## Definition of Done (Part 10)

- [ ] Desktop app fully wired to the real runtime — **frontend verified** (12 Playwright e2e + axe against a real
      sandboxed runtime, 2026-09-28); **Tauri shell (tray, global hotkey, indicator, sidecar restart) not verified in a
      recorded live session** → live session item L3
- [x] App API versioned (`/api/v1`), typed (schema.json → types.ts, contract test fails on drift), secured; contract +
      security tests pass (`tests/integration/test_app_api.py`, `test_app_api_contract.py`)
- [x] UI security (Part 4): CSP, bundled assets, SafeMarkdown + XSS fixtures, approval ID/hash/gesture/isTrusted,
      write-only secrets, minimal capabilities, **shell navigation guard** (`navguard.rs`). **Verified on Windows
      2026-09-28** in `pnpm tauri dev` over WebView2 CDP: the app's 3 pages load; `location` change, `window.open`,
      middle-click and click on a `target=_blank` link, and a dropped link (CDP drag events) all left the window on the
      app, opened no new webview or window, and loaded example.com nowhere. A real OS drag-and-drop with the mouse → L3
- [x] Latency — warm runtime (what the app sees), Groq, 2026-09-28: fast path 0.03–0.96 s (target < 1.5 s); first text
      0.52–0.81 s for simple questions (target < 3 s; baseline 3.31 s whole reply, local 8B 25–35 s). One-shot CLI still
      pays ~2 s runtime start. Windows re-measure with local models → L6
- [x] Cloud + fallback chain live (2026-09-28, docs/ACCEPTANCE_REPORT.md "Live cloud verification"): routing, streaming,
      tool calling, vision provider, cloud STT (after fixing its upload bug) + local STT fallback, in-provider fallback
      and auth → local → degraded. Sub-agents: PARTIALLY (run on a real model; the Groq free-tier TPM budget runs out).
      Local-model step of the chain → L5 on Windows
- [ ] Voice live with a real microphone → L2
- [ ] 12-test acceptance suite re-run live → L1 (last full run 2026-09-27, pre-app, in docs/ACCEPTANCE_REPORT.md)
- [ ] Game/full-screen awareness + default browser — implemented and unit-tested (`gamemode.py`,
      `tools/browser/default.py`, ADR 0015); live check with a real full-screen app → L4
- [ ] Installer builds, installs, runs, uninstalls on a clean profile → L7 (Windows Home: no Sandbox; use a separate
      local user profile)
- [x] Automated tests pass — **on Windows, 2026-09-28**: pytest 432 passed / 6 skipped (the live tests; baseline
      2026-09-27: 340); ruff clean; pyright 0 errors; tsc clean; Vitest 26/26; Playwright e2e 12/12 incl. axe on Edge;
      `cargo test` 2/2 (navguard, first Windows compile); `cargo clippy --all-targets` clean
- [x] Visual review — screenshots of 15 screens × light/dark × 1×/1.5× in docs/screenshots (commit 3b422c1), issues
      fixed there. Real-DPI multi-monitor check → L3
- [ ] CLI still works exactly as documented — pytest CLI tests pass; manual Windows spot-check → L1
- [x] Documentation matches reality — docs/UI.md added; app sections in architecture, security, development,
      testing, troubleshooting, README (2026-09-28), each claim checked against code
- [x] No secrets committed (history searched for the Groq key prefix: none); repo clean; work committed

## Current item

Step 1 (non-live, Windows) is done — see "Session 2026-09-28c". Next: the batched live session L1–L7 (needs the
user's go-ahead).

### Live session plan (~20 min, one batch; check foreground/full-screen before each step)

- L1 `SCAR_LIVE_TESTS=1 uv run python scripts/acceptance/run_acceptance.py` (12 tests) + `SCAR_LIVE_TESTS=1 uv run pytest -q`
- L2 voice: real mic, push-to-talk, wake word, cloud STT + local fallback, TTS + fallback, barge-in, voice approval
  (own speech must not approve), unplug/switch mic
- L3 installed app walkthrough: tray, Quick Bar hotkey (< 150 ms), window < 2 s, indicator, approvals end to end,
  toast for hidden window, kill switch, crash → restart banner, 150 % scaling, idle RAM of shell + WebView2
- L4 game mode with a full-screen window; "open example.com" opens the default browser (Opera GX)
- L5 cloud: tool calling on Groq, forced failures (bad key → alternative → local → degraded), sub-agent with real model
- L6 `uv run python scripts/bench/warm_latency.py` (warm fast path, TTFT)
- L7 `uv run python scripts/build_installer.py`, install under a second Windows user, onboarding, 3 commands, uninstall

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

Streaming: **none** at baseline. After commit 1fafb9b: OpenAI-compatible providers stream; first text ≈0.5 s on Groq;
system.info 4.5 s → 0.75 s.

Finding during baseline: the audit's state-question guard (F-16) matched definitions ("what is RAM?") and answered
with the user's RAM usage → fixed (requires a possessive/now cue) with 11 parametrised cases.

## Session 2026-09-28 (cloud container, Linux) — findings and fixes

- 12 tests depended on Windows (cmd, PowerShell, UNC, Edge/Opera, llama-server.cmd) without the `windows` marker →
  marked. They still run on Windows.
- `scar doctor` crashed with OSError when a dependency's native library failed to load (PortAudio) → reported as a
  failed check; regression test `tests/unit/test_doctor.py`. The app's Diagnostics showed "Internal error" before.
- PDF writer crashed on list bullets when Arial is unavailable → ASCII bullet fallback.
- Lint: an unannotated broad `except` in the browser manager → annotated.
- `app/tsconfig.tsbuildinfo` (build output) was committed → untracked + ignored.
- Quick Bar mic button said "Hold to talk" but works on a click → "Talk (push to talk)".
- No shell-level navigation guard (Part 4) → `navguard.rs`.
- e2e config hard-wired Edge → `SCAR_E2E_CHROMIUM` override for non-Windows runs.
- Known limitation (documented): the terminal voice mode's push-to-talk and the Quick Bar share Ctrl+Alt+Space by
  default; the app itself has no global push-to-talk hotkey (mic button). Defaults kept so the CLI behaves as
  documented.

## Session 2026-09-28b — live Groq (network opened by the user)

Six bugs found live and fixed with tests: knowledge-question shortcut vs folder/path questions; bare finish JSON
shown as the reply; OTPM refusals not retried with fewer tokens; transient errors blocking the short rate-limit wait
(max wait 15 → 30 s); agent.delegate never offered; Groq Whisper upload sent Content-Type application/json (cloud STT
never worked). gpt-oss-20b added as the third Groq reasoning model. pytest 306 passed / 118 skipped.

Known limit: Groq free tier (7–8K tokens/min per model, ~3.6–5.3K per tool step) → multi-step and multi-agent tasks can
hit rate limits; SCAR waits ≤ 30 s, then fails honestly keeping what was done. Adding GEMINI_API_KEY gives a second quota.

## Session 2026-09-28c — Windows, step 1 (non-live)

- Cloud branch pulled (fast-forward from main 65d52a4); `uv sync`, `pnpm install` clean.
- Tests and lint: see the Definition of Done above. The shell compiled on Windows for the first time; clippy clean.
- `pnpm tauri dev`: shell + runtime up in 23 s (debug build, cold Vite); navigation guard verified live (see UI
  security). Shut down afterwards; nothing left running.
- Installer: `uv run python scripts/build_installer.py` → `SCAR_1.0.0_x64-setup.exe`, 13.49 MiB. Not installed yet → L7.
- CLI: `scar doctor` (Opera GX automation browser, Groq reachable, STT/TTS chains), `scar status`, `scar --help`,
  and 3 requests (fast path RAM, a knowledge question on Groq, a file count in a sandbox folder): all correct.
- **Honesty bug found in the owner's own task history and fixed.** "open whatsapp … and draft a message to tuff
  saying hi" was reported **succeeded**. `message.send` had come back unavailable (WhatsApp not connected, so
  nothing was sent), and only `apps.launch` was verified. The verifier now requires a real send (`message.send`,
  `email.send`, `email.reply`) or draft (plus `email.draft`, `input.type`, `uia.act`) for messaging requests.
  Questions ("how do I send an email…") and file tasks ("write text to notes.txt") are excluded. 13 parametrised
  cases, a replay of the recorded task, and a words-only claim test.
- `gen_notices.py` was not deterministic (hash order for same-named crates, CRLF on Windows), so every build dirtied
  THIRD_PARTY_NOTICES.md. Now sorted by name/version/license and written with LF; identical across runs.
- Cosmetic (kept): a file count done with a read-only `Get-ChildItem` via `terminal.run` gets "(I couldn't verify
  this automatically.)" because terminal commands count as side effects. Conservative, not wrong.
- Leftover from this session's own test (removed): a stale `app-api.json`, after force-closing the dev shell's
  process tree ~1 s after the runtime accepted `/shutdown`. That was the test procedure, not the Quit path, and the
  shell health-checks the port before trusting the file anyway.

## Decisions

ADRs 0014 (Tauri 2), 0015 (browsers), 0016 (licenses).

## Blockers

- Windows Sandbox: not available on Home → clean-install test uses a separate profile/data dir.
- Live items L1–L7 need the Windows machine and a free ~20-minute slot from the user.
- Accounts (Google, Microsoft, Telegram, Discord, WhatsApp, GitHub): need the user's credentials; flows show "Needs: …".
