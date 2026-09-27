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
| CLI entry (`scar`, subcommands) | WORKING after fixes | F-01, F-02 |
| Desktop UI | MISSING | No desktop app exists; CLI/REPL only. Building one changes product scope → needs owner decision |

(filled in as the audit proceeds)

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

## Test evidence

(appended as runs complete)

## Next steps

1. Realistic user-command pass through the one-shot CLI on the fresh DB.
2. Provider fallback live test (invalid cloud key → local → degraded).
3. Failure-path tests (model down mid-task, tool errors, cancellation, malformed model output).
4. Live injection test (served page with instructions).
5. Architecture / docs review, setup checklist, final report.
