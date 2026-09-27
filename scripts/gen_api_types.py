"""Write app/src/api/schema.json from the App API's Pydantic models and event classes; `pnpm gen:types` turns it into
app/src/api/types.ts. tests/integration/test_app_api_contract.py fails if the committed schema is stale.

    uv run python scripts/gen_api_types.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel  # noqa: E402

from scar.api.models import SCHEMA_MODELS  # noqa: E402
from scar.core import events  # noqa: E402

OUT = ROOT / "app" / "src" / "api" / "schema.json"


def event_models() -> list[type[BaseModel]]:
    return [c for c in vars(events).values() if isinstance(c, type) and issubclass(c, events.Event) and c is not events.Event]


def _strip_property_titles(schema: dict[str, object]) -> dict[str, object]:
    """Property titles make json2ts emit one alias type per field; without them fields are typed inline."""
    props = schema.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            if isinstance(sub, dict):
                sub.pop("title", None)
    return schema


def build() -> dict[str, object]:
    defs: dict[str, object] = {}
    for model in [*SCHEMA_MODELS, *event_models()]:
        schema = model.model_json_schema(mode="serialization", ref_template="#/definitions/{model}")
        for name, sub in schema.pop("$defs", {}).items():
            defs[name] = _strip_property_titles(sub)
        defs[model.__name__] = _strip_property_titles(schema)
    return {"$schema": "http://json-schema.org/draft-07/schema#", "title": "ScarApi", "type": "object",
            "properties": {name: {"$ref": f"#/definitions/{name}"} for name in sorted(defs)}, "definitions": defs,
            "additionalProperties": False}


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
