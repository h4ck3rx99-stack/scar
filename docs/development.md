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
