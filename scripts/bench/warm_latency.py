"""Latency inside a warm runtime (what the desktop app and daemon see): time to first streamed text and to the
final reply, per request. Usage: uv run python scripts/bench/warm_latency.py [--json out.json]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.settings import load_settings  # noqa: E402
from scar.runtime.runtime import Runtime  # noqa: E402

REQUESTS = ["what's using my RAM", "remember that my benchmark colour is teal", "In one sentence, what is RAM?",
            "In one sentence, what is a GPU?", "Give me three tips for writing clear commit messages."]


async def main(out: Path | None) -> None:
    rt = Runtime(load_settings())
    await rt.start(with_hotkeys=False)
    results = []
    try:
        assert rt.tasks is not None
        for text in REQUESTS:
            first: list[float] = []
            t0 = time.perf_counter()

            def on_event(ev, t0=t0, first=first):  # type: ignore[no-untyped-def]
                if ev.kind == "assistant_delta" and not ev.reset and ev.text and not first:
                    first.append(time.perf_counter() - t0)

            rt.services.bus.add_listener(on_event)
            task = await rt.tasks.run(text)
            total = time.perf_counter() - t0
            rt.services.bus.remove_listener(on_event)
            route = rt.services.router.last_route.get("reasoning") or rt.services.router.last_route.get("fast") or "fast path"
            row = {"request": text, "total_s": round(total, 2), "first_text_s": round(first[0], 2) if first else None,
                   "status": task.status.value, "route": route, "reply": task.result_summary[:80]}
            results.append(row)
            print(f"{total:6.2f}s total  first text {row['first_text_s']}s  [{route}]  {text}")
    finally:
        await rt.stop()
    if out:
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    asyncio.run(main(ap.parse_args().json))
