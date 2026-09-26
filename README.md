# SCAR — Systemic Cognitive Autonomous Responder

SCAR is an AI operator for your Windows 11 computer. Tell it what you want, typed or spoken, and it does it.

It opens apps and projects, runs commands and tests, reads the screen, drives a browser, researches the web, and
manages files. It also handles reminders and monitors, and sends email and messages once configured. It checks
that each action actually worked and asks before anything risky.

```
> open VS Code in my BISense folder
Opened BISense in Visual Studio Code.
> run the tests in C:\Projects\api
2 test(s) failed, 41 passed. Failing: tests/test_auth.py::test_refresh, tests/test_auth.py::test_expiry.
> find the failing test and fix it
  Running tests.
  Writing auth.py.
  Running tests.
Fixed the expiry check in auth.py; all 43 tests pass.
```

It runs at **zero recurring cost**: free cloud providers first, your local models (Ollama or llama.cpp) as a
fallback, and a deterministic fast path when no model is reachable.

## Quick start

Requirements: Windows 11, Python 3.11, [uv](https://docs.astral.sh/uv/). Optional: an NVIDIA GPU, a microphone,
Chrome or Edge.

```
git clone <this repo> scar && cd scar
uv sync
uv run playwright install chromium        # only if neither Chrome nor Edge is installed
uv run scar doctor                        # checks everything and tells you what to fix
```

Give SCAR a model (any one of these):

* **Free cloud (recommended)**: get a key at console.groq.com and run `uv run scar config set-secret GROQ_API_KEY`.
  Gemini (`GEMINI_API_KEY`), OpenRouter, Cerebras and others also work; see [docs/providers.md](docs/providers.md).
* **Local**: have Ollama running, or point SCAR at a GGUF and it runs llama.cpp itself:
  `uv run scar config set local_llm_model_path C:\models\qwen3-8b-q4_k_m.gguf`

Then:

```
uv run scar                               # interactive prompt
uv run scar "what's using my RAM"         # one-shot
uv run scar --voice                       # voice (push-to-talk Ctrl+Alt+Space; typing still works)
uv run scar daemon start                  # keep reminders and monitors running in the background
```

Emergency stop: **Ctrl+Alt+Shift+K** halts input, cancels tasks, kills child processes and stops speech.

## Examples

* "Open VS Code in C:\Projects\site, start the dev server, and tell me when it's ready"
* "Look at my screen — what does this error mean?"
* "Research the Python walrus operator and save a summary with sources to walrus.md"
* "Watch process 4412 and tell me if it crashes"
* "Remind me every weekday at 9am to check the build"
* "Find the file in Documents that mentions the Q3 budget"
* "Remember that my BISense folder is at C:\Projects\BISense"
* "Email this report to Sarah" (after email is configured; always asks first)

## How it stays safe

The runtime, not the model, makes every permission decision:

* **Per-action risk**: LOW, MEDIUM, HIGH or CRITICAL.
* **Autonomy levels** (default 3): LOW and MEDIUM run automatically, HIGH asks first, and CRITICAL (payments,
  permanent deletion, protected paths) needs a typed confirmation code.
* **Guards**: a path guard that sees through junctions, short names and streams, and a command guard that uses
  PowerShell's own parser.
* **Injection defence**: taint tracking of anything that came from web pages, emails, files or the screen, and
  anchoring every action to what you actually asked for.
* **Secrets**: redaction everywhere, secrets kept in Windows Credential Manager, and an append-only audit log.

Details: [docs/security.md](docs/security.md).

## Commands

| Command | |
|---|---|
| `scar` / `scar "<objective>"` / `scar --voice` | REPL, one-shot, voice |
| `scar doctor [--deep]` | environment, providers, devices, security checks |
| `scar status` | tasks, monitors, loaded models, resources, provider health, data sent this session, permissions |
| `scar config show\|set\|validate\|path\|set-secret` | configuration ([docs/configuration.md](docs/configuration.md)) |
| `scar logs [-f] [--task ID] [--level]` | logs and per-task traces |
| `scar tasks list\|show\|cancel` | task history |
| `scar permissions list\|revoke\|policy` | grants and policy |
| `scar memory list\|search\|forget\|wipe` | long-term memory |
| `scar providers list\|test` | providers |
| `scar daemon start\|stop\|status\|install\|uninstall` | background runtime; `install` adds a logon task (asks first) |
| `scar voice devices\|test` | audio |
| `scar auth google\|microsoft\|telegram` | sign-in for integrations |

Flags: `--verbose` (tools and timings), `--debug` (providers, fallbacks), `--autonomy N`, `--dry-run`, `--no-daemon`.

## Capability status

Statuses as defined in the build contract: **VERIFIED** means it was exercised end to end on the reference machine
with evidence; **IMPLEMENTED-UNVERIFIED** means the code is complete and tested with doubles, but live verification
needs the named prerequisite. Evidence is in [docs/ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md).

<!-- status-table -->
See [docs/ACCEPTANCE_REPORT.md](docs/ACCEPTANCE_REPORT.md).
<!-- /status-table -->

## Documentation

[architecture](docs/architecture.md) · [configuration](docs/configuration.md) · [security](docs/security.md) ·
[providers](docs/providers.md) · [tools](docs/tools.md) · [memory](docs/memory.md) · [voice](docs/voice.md) ·
[browser](docs/browser.md) · [integrations](docs/integrations/) · [development](docs/development.md) ·
[testing](docs/testing.md) · [troubleshooting](docs/troubleshooting.md) · [environment](docs/ENVIRONMENT.md) ·
[ADRs](docs/adr/)
