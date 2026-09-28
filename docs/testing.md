# Testing

```
uv run pytest -q                                   # everything safe to run anywhere
SCAR_LIVE_TESTS=1 uv run pytest -q -m live         # live desktop tests (open windows, type, click)
SCAR_LIVE_TESTS=1 uv run python scripts/acceptance/run_acceptance.py [--only 1,3]   # acceptance D3.1–D3.12
uv run python scripts/acceptance/measure_resources.py                             # resource acceptance D4

cd app && pnpm test                                # frontend unit, component, store and sanitizer tests
cd app && pnpm e2e                                 # UI end-to-end + axe accessibility against a real sandboxed runtime
cd app && SCAR_E2E_CHROMIUM=/path/to/chrome pnpm e2e   # the same with a given Chromium (non-Windows / CI)
```

## Layers

| Folder | What |
|---|---|
| `tests/unit` | provider routing and the full fallback taxonomy (scripted clients, respx HTTP mocks), structured-output repair, rate-limit parsing, core primitives, config precedence, memory, time parsing, scheduler (incl. missed firings across restart), monitors (event-based process waits, folders, downloads), fast path, parsers |
| `tests/integration` | the real tool pipeline on the real OS: filesystem, terminal (timeouts, caps, cancellation, tree kill), agent loop with a scripted model (states, persistence, planning, verification honesty, loop prevention, cancellation, degraded mode, sub-agents), local model lifecycle (start on demand, idle unload, reuse without killing, admission), headless browser against a local fixture server, dev server readiness and crash detection, CLI commands, communications integrations with recorded HTTP fixtures |
| `tests/security` | path guard tricks + property tests, command guard adversarial corpus + property tests, policy/autonomy/grants/approvals/TOCTOU, prompt-injection corpus driven by an adversarial scripted model, secrets never in logs/traces/model payloads/DB/child processes, test-double isolation, no stub markers |
| `app/src/**/*.test.ts(x)` | Vitest: event reducer (outcomes: verified, unverifiable, failed, denied), `SafeMarkdown` XSS fixtures |
| `app/e2e` | Playwright + axe: onboarding, streaming answer, approve/deny, cancel, Stop All, key test flow (mock provider), memory edit/forget, permission revoke, tasks and diagnostics, keyboard-only, offline. The runtime is `scripts/e2e_runtime.py`: sandboxed data dir, in-memory keyring, local mock model. `screenshots.spec.ts` captures every screen in both themes at 1× and 1.5× into `docs/screenshots/` |
| `scripts/acceptance` | the 12 end-to-end acceptance scenarios, run for real |

## Markers

`windows` (skipped off Windows), `live` (skipped unless `SCAR_LIVE_TESTS=1`), `network`, `gpu`, `audio`, `credentials`.
Every skip prints its reason.

## Safety guarantees

* Every test gets a sandbox: a fresh allowed root, data folder and config folder (`tests/conftest.py`). An autouse
  guard fails any test that creates files in the repository, Desktop or Documents.
* Communication providers refuse real sends while `SCAR_TESTING=1` unless `SCAR_LIVE_TESTS=1` **and** the recipient
  equals the configured test recipient (`SCAR_TEST_EMAIL_TO`, `SCAR_TEST_TELEGRAM_CHAT`,
  `SCAR_TEST_DISCORD_CHANNEL`, `SCAR_TEST_WHATSAPP_TO`).
* Notification toasts are disabled in tests. Destructive tools run only against sandbox files.
* The acceptance harness creates its fixtures under `~/scar-sandbox/acceptance-<timestamp>` and closes only the
  windows and processes it started.

## Test doubles

`tests/helpers.py`: `ScriptedChatClient` (plugged into the router via its client factory),
`FakeEmbeddings` (deterministic hashing), `install_scripted_router`. They are importable only from tests.
