# Development

```
uv sync                        # install (Python 3.11, uv 0.12+)
uv run scar doctor             # environment check
uv run pytest -q               # full suite (Windows-live tests skipped unless SCAR_LIVE_TESTS=1)
uv run ruff check src tests    # lint
uv run pyright                 # strict typing on core, security, agent, tools/base
uv run python scripts/gen_docs.py   # regenerate tool catalog, settings table, .env.example
uv run pip-audit               # dependency vulnerabilities
```

## Layout

See [architecture.md](architecture.md). Rules of the codebase:

* The runtime is the security boundary. New capabilities go through the tool pipeline; nothing calls `tool.run`
  directly.
* Frozen interfaces (`core/types.py`, `tools/base.py`, `runtime/services.py`, the policy API, the event bus) change
  only with an ADR in `docs/adr/`.
* No stubs in runtime code (a test rejects `raise NotImplementedError`, TODO and FIXME under `src/scar`). A missing
  external prerequisite raises `CapabilityUnavailable(prerequisite, setup_doc)`.
* Test doubles live only under `tests/` (a test asserts `src/scar` never imports from `tests`).
* Blocking and COM work stays off the event loop (`to_thread`, the UIA worker, the DB thread).
* argv lists only, never `shell=True`. Paths go through `path_guard`, commands through `command_guard`.
* Explicit error types; no bare `except`. A broad `except Exception` needs a `# noqa: BLE001 - reason`.

## Adding things

* **Tool**: [tools.md](tools.md#adding-a-tool).
* **Provider**: an entry in `providers_default.yaml` (or the user `providers.yaml`). OpenAI-compatible services need
  no code. A new API shape gets an adapter in `providers/llm/` implementing `chat()` and `list_models()` and raising
  `ProviderError` with the right kind.
* **Policy defaults**: `config/policy_default.yaml`.
* **Prompts**: versioned templates in `agent/prompts/*_v1.md`. Bump `PROMPT_VERSION` for meaningful changes.
* **Migrations**: append a new version to `storage/migrations/__init__.py`; never edit an applied one.

## Build ledger

`BUILD_LEDGER.md` tracks the Definition of Done, evidence and blockers.

## Desktop app

The frontend is in `app/` (React + TypeScript + Vite), the shell in `app/src-tauri/` (Tauri 2, Rust).
Prerequisites: Node.js and pnpm; for the shell, Rust (MSVC toolchain), Visual Studio C++ build tools and WebView2.

```
cd app
pnpm install
pnpm typecheck                 # tsc
pnpm test                      # Vitest unit/component tests
pnpm build                     # production frontend → app/dist
pnpm tauri dev                 # the shell in dev mode (Vite dev server on :1420, debug build uses the repo .venv)
pnpm e2e                       # Playwright end-to-end tests (see testing.md)
cd src-tauri && cargo test     # shell unit tests (navigation guard); Windows
```

API types: after changing `src/scar/api/models.py` or the event classes, regenerate and commit both files:

```
uv run python scripts/gen_api_types.py
cd app && pnpm gen:types
```

Icons: `uv run python scripts/make_icons.py`. Third-party notices: `uv run python scripts/gen_notices.py`.

Installer (Windows):

```
uv run python scripts/build_installer.py
```

It builds SCAR's wheel, exports hash-locked runtime requirements, bundles `uv.exe`, regenerates the notices and runs
`pnpm tauri build`. Output: `app/src-tauri/target/release/bundle/nsis/SCAR_<version>_x64-setup.exe`. The installer
is unsigned. There is no auto-updater: to update, run the new installer over the old one (data is kept).
