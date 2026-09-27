"""The desktop app's TypeScript types are generated from the runtime's models; this fails when they drift.

Fix: `uv run python scripts/gen_api_types.py` then `pnpm --dir app gen:types`."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _gen() -> object:
    spec = importlib.util.spec_from_file_location("gen_api_types", ROOT / "scripts" / "gen_api_types.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_schema_matches_the_models() -> None:
    committed = (ROOT / "app" / "src" / "api" / "schema.json").read_text(encoding="utf-8")
    assert committed == _gen().render(), "API schema is stale: run scripts/gen_api_types.py and pnpm gen:types"  # type: ignore[attr-defined]


def test_generated_typescript_declares_every_definition() -> None:
    schema = json.loads((ROOT / "app" / "src" / "api" / "schema.json").read_text(encoding="utf-8"))
    ts = (ROOT / "app" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
    missing = [name for name in schema["definitions"] if f"export interface {name} " not in ts and f"export type {name} " not in ts]
    assert not missing, f"types.ts is stale (missing {missing[:5]}): run pnpm --dir app gen:types"


def test_event_kinds_are_unique() -> None:
    from scar.core import events

    kinds = [c.model_fields["kind"].default for c in vars(events).values()
             if isinstance(c, type) and issubclass(c, events.Event) and c is not events.Event]
    assert len(kinds) == len(set(kinds))
