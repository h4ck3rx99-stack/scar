# SCAR Audit Ledger

Independent production-readiness audit (started 2026-09-27). Read this first when resuming; continue from
"Next steps". Claims in BUILD_LEDGER.md / docs/ACCEPTANCE_REPORT.md are treated as unverified until re-checked here.

## Method

Inspect code paths → run the real CLI as a user → break failure paths → fix → add regression tests → re-run.
Live actions only touch `~/scar-sandbox` and windows the test created.

## Capability checklist

Legend: WORKING (verified live this audit) · PARTIAL · BROKEN · MISSING · BLOCKED (external credential) · NOT VERIFIED

| Area | Status | Evidence / note |
|---|---|---|
| Reasoning / agent loop / planning | WORKING | live CLI: multi-step E2E fix workflow; planner for multi-clause objectives; 19 agent tests |
| Tool calling, registry (114 tools), tool selection | WORKING | relevance-ranked selection with hints; 110 tools, 97 referenced by tests |
| Permission engine, approvals, autonomy 0–4 | WORKING | 150 security tests; live approval prompt observed |
| Verification ("done" only when done) | WORKING after F-07/F-08 | objective checks + postconditions; false-success regression tests |
| Provider routing / fallback / degraded mode | WORKING | live: invalid Groq key → Ollama (down) → llama.cpp; no model → honest degraded reply |
| Resource-aware routing | WORKING | live: GPU offload reduced to 21 layers while a game used VRAM; idle unload; eviction of idle VLM |
| Local inference (llama.cpp managed, Ollama) | WORKING (llama.cpp) / NOT VERIFIED (Ollama: not running here) | every live test ran on local Qwen3-8B |
| Cloud inference | BLOCKED (credential) | HTTP-mock tests; no keys on this machine |
| Memory (remember/recall/alias/forget) | WORKING | live CLI; smoke tests |
| Filesystem | WORKING | live create/find/summarise; smoke tests |
| Terminal / PowerShell | WORKING | acceptance D3.3/D3.9; command-guard tests |
| Process management | WORKING | smoke test start/list/inspect/wait/stop |
| Windows automation (windows, UIA) | WORKING | live VS Code / Notepad open+close; 6 live desktop tests (pre-audit run) |
| Keyboard / mouse / clipboard | WORKING (pre-audit live run) | not re-run during the audit to avoid disturbing the user's session |
| Screenshot / OCR / vision | WORKING | live "what is on my screen?" (F-11 fixed) |
| Browser automation | WORKING | live one-shot handoff (F-12); 13 browser actions headless-tested |
| Web research | WORKING | D3.10; research test with local-network filtering |
| Voice input / STT / TTS | PARTIAL | synthetic-speech pipeline + session tests pass; live mic/wake word not re-verified |
| Voice approval | WORKING (tests) | policy tests: voice cannot confirm CRITICAL |
| Email / messaging / contacts | BLOCKED (credential) | fixture tests; honest not-connected replies (F-17) |
| Calendar | WORKING (local/.ics) / BLOCKED (Google, Outlook) | F-15 caveat; create/update/list tests |
| Reminders / scheduling / notifications | WORKING | smoke tests; daemon run |
| Git | WORKING | smoke test status/diff/commit/branch/log/stash/clone |
| GitHub | BLOCKED (credential) | mocked-API tests |
| Coding workflows | WORKING | live E2E: diagnose → edit → re-run → report |
| Multi-agent (sub-agents) | NOT VERIFIED live | scripted test only |
| Long-running tasks / cancellation | WORKING | cancellation tests; daemon background task |
| Prompt-injection protection | WORKING | 150 security tests; live hidden-instruction page resisted |
| Logging / observability / diagnostics | WORKING | `scar logs`, `status`, `doctor`; fallbacks visible (F-21) |
| CLI | WORKING after F-01/F-06/F-14 | all groups exercised |
| Desktop UI | MISSING | CLI/REPL only — building one changes scope; owner decision |
| Configuration | WORKING | `config set/show/validate`; SCAR_MODELS_DIR added |
| Documentation | UPDATED | docs/SETUP.md added; README table from audit evidence |
| Tests | 340 passed, 6 skipped (live desktop) | ruff clean, pyright strict 0 errors |

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| F-01 | Medium | `scar config`, `tasks`, `permissions`, `memory`, `providers`, `daemon`, `voice`, `auth` without a subcommand failed with "Missing command" (spec requires `scar config` to work) | FIXED: each group runs a sensible default (`config show`, `tasks list`, …) |
| F-02 | Low | `scar doctor` reported "Windows 10" on Windows 11 (build 26200) | FIXED |
| F-03 | Medium | Acceptance harness wrote into the user's real SCAR database (tasks, memories such as "folder alias 'my acceptance project'", monitors) — `scar status` showed only test data | FIXED: harness runs in an isolated per-run data dir (`scripts/acceptance/isolation.py`); new `SCAR_MODELS_DIR` setting shares downloaded models. Polluted DB moved (not deleted) to `~/scar-sandbox/pre-audit-userdata/` |
| F-04 | Medium (security) | `browser.type`: if the field's type/autocomplete could not be read, the password/payment check failed open | FIXED: fails closed unless the CRITICAL-approved `sensitive_field=true` path is used |
| F-05 | Low | Event bus silently swallowed listener exceptions | FIXED: logged once per listener |
| F-06 | Low (UX) | `scar tasks` / `scar memory` printed nothing when empty; memory/task text was interpreted as Rich markup (`[alias]` vanished, text with brackets garbled) | FIXED: empty states + escaping |
| F-07 | **Critical** | Tasks were reported `succeeded; verified=True` without doing what was asked: "inspect the failing tests, fix it, run the tests again" stopped after the diagnosis. The verifier only checked that the actions taken succeeded, never that the objective's required actions happened | FIXED: `objective_checks()` derives deterministic requirements from the user's words (a file change for fix/edit requests on code; a passing test run after the change); unmet → the model is sent back to work (bounded), then FAILED with the reason. 3 regression tests. Live E2E now fixes, re-runs and reports (215 s on local 8B) |
| F-08 | **High** | The executor's "narrated instead of acted" nudge regex contained literal backspace bytes (0x08) instead of `\b` since it was committed, so it never matched — BUILD_LEDGER's claimed fix was ineffective | FIXED + guard test `test_no_control_characters_in_source` |
| F-09 | **High (security)** | Exfiltration control only matched when the outgoing value was itself a fragment of local content; a URL wrapping leaked private text (`https://x/?q=<diary text>`, also percent-encoded) was auto-allowed | FIXED: 24-char windowed containment on raw and URL-decoded values; 2 regression tests |
| F-10 | High (usability) | Reading a file that `fs.search` had just found in the user's own project required approval (taint on read-only paths/URLs), stalling one-shot tasks; spec C4.9 lists no read-only case | FIXED: read-only tools don't escalate on tainted path/url; deletions/commands/comms still do (tested) |
| F-11 | Medium (UX) | "What is on my screen?" returned a raw OCR dump (acceptance D3.4 only checked a phrase appeared in it) | FIXED: tool-less model turns the evidence (wrapped as untrusted) into a 2–4 sentence answer; falls back to the evidence summary with no model |
| F-12 | Medium (UX) | `scar "open chrome and go to X"` (one-shot) closed the browser when the command exited — the page flashed and vanished (acceptance D3.2 ran inside a long-lived harness and missed it) | FIXED: when a one-shot task's result is a page, it is handed to the user's own Chrome/Edge before exit |
| F-13 | **High** (reliability) | Provider health locked any UNAVAILABLE provider for 10 years (Ollama not started yet, local server failed once) — a long-running daemon never recovered. The `config_fingerprint` meant to clear AUTH lockouts on a key change was never written or read, and cached clients kept an old key | FIXED: UNAVAILABLE re-probed after 60 s; AUTH holds until the credential fingerprint changes; client cache keyed by fingerprint; `scar status` shows plain states ("retry in 45s", "after the key is fixed"). Test added |
| F-14 | Low | `scar daemon start` printed the venv launcher PID, not the daemon's | FIXED |
| F-15 | Medium (honesty) | "What's on my calendar today?" opened Google Calendar in the user's browser and reported success; after that was fixed, SCAR's empty *local* calendar was reported as "your calendar shows no events" | FIXED: calendar tool hint + prompt rule (no website substitution); tools can attach a must-tell `user_caveat` that the runtime appends when the model omits it |
| F-16 | Medium (honesty) | Current-state questions were answered by repeating an earlier answer from conversation history / task-history memory, with zero tool calls | FIXED: state-question guard sends the model back to check once; memory notes labelled possibly outdated; task-history memory only for tasks that changed something. Test added |
| F-17 | Low (UX) | "Send a Telegram message to Alice" asked for Alice's *email address*; with no email account, the error named only Outlook | FIXED: clarification asks for the channel's handle (channel inferred from the request); all email options listed |
| F-18 | Medium (tests) | Voice had no automated tests (BUILD_LEDGER's "loopback verified" was an ad-hoc run, no script) | FIXED: silent pipeline tests (SAPI → Silero VAD endpointing → faster-whisper transcript) and voice session tests (spoken task, spoken result, stop) |
| F-19 | Low | Silero VAD failure silently degraded to energy VAD | FIXED: logged |
| F-20 | Medium (honesty) | When the model became unreachable mid-task, the reply hid the actions already done | FIXED: "I lost the language model partway through… Already done: …". Test added |
| F-21 | Low (observability) | Candidate-level provider failures (bad key, server down) were not logged and not shown as fallbacks | FIXED: `provider_error` logs + `⇄ groq → llamacpp/local` in `--debug` (verified live with an invalid key) |
| F-22 | Medium (security) | `web.research` fetched any URL a search engine returned, including local-network addresses (router, NAS, cloud metadata); `web.fetch`'s local-network check missed 172.16/12 and IPv6 | FIXED: research skips local hosts the user did not name; `ipaddress`-based detection. Tests added |
| F-23 | Low (UX) | Approval reasons were internal jargon ("arguments come from external content: … came from fs-search:C:…") | FIXED: "this uses details you didn't give yourself — the path … came from a file search in …" |
| F-24 | Low (UX) | Updating an event in SCAR's own local calendar required approval (HIGH) while creating one did not | FIXED: MEDIUM for the local calendar; Google/Outlook updates stay HIGH |
| F-25 | Low (UX) | REPL gave no hint that no cloud model is set (slow answers with no explanation); `scar status` drew an empty tasks table | FIXED: one-line model note in the banner; empty states |
| F-26 | Medium (tests) | 62 of 110 tools were never referenced by any test | FIXED: 22 smoke tests; 13 remain (GUI-only or credential-only: apps.open, browser.upload, dev.install, email.draft/reply/search, git.integrate, message.recent, system.media, uia.inspect, vision.ask, windows.wait) |

## Test evidence

* Full suite: 340 passed, 6 skipped (live desktop tests, gated by SCAR_LIVE_TESTS=1); ruff clean; pyright strict 0 errors.
* Live CLI (one-shot, real local model, sandbox `~/scar-sandbox/audit-ux`): system resources; remember/recall; create a
  file; find a file by content; summarise a document; open VS Code in a folder; start/close Notepad; open Chrome at a
  URL (page kept open); what is on my screen; run tests; end-to-end find-and-fix (215 s); calendar question;
  Telegram/email without credentials; invalid Groq key fallback; no-model degraded mode; hidden prompt-injection page.
* Daemon: start → background task → status → stop; no processes left behind.
* Windows opened by tests were closed by the tests. Exception: the calendar question (before F-15) opened Google
  Calendar in the user's default browser (Opera); it was left open because it may have joined an existing window.

## Remaining work (owner decisions / external)

1. Desktop UI: none exists. Building one is a scope decision for the owner.
2. Add at least one free cloud key (Groq or Gemini) to verify cloud inference and fallback live; local-only answers
   take 20–60 s on this laptop.
3. Connect accounts (Gmail/Outlook, Telegram, Discord, WhatsApp, GitHub) to verify the BLOCKED integrations live.
4. Re-verify voice live (microphone + wake word) when the machine is free: `uv run scar voice test`.
5. Re-run the full acceptance suite when the desktop can be used by tests (`SCAR_LIVE_TESTS=1 uv run python
   scripts/acceptance/run_acceptance.py`); it opens windows, so it was not re-run during the user's session.
