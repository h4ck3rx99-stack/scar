"""End-to-end CLI latency: wall time of `scar "<request>"` (process start to exit), as a user experiences it.

Usage: uv run python scripts/bench/latency.py [--runs N] [--json out.json]
Requests are read-only or write only to SCAR's own memory; nothing opens windows.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUESTS = {
    "fast_path_sysinfo": "what's using my RAM",
    "fast_path_memory": "remember that my benchmark colour is teal",
    "simple_question": "In one sentence, what is RAM?",
}


def run_once(text: str) -> tuple[float, str, int]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "scar", text], capture_output=True, text=True, env=env, cwd=ROOT,
                       timeout=600, encoding="utf-8", errors="replace")
    return time.perf_counter() - t0, (p.stdout.strip().splitlines() or [""])[-1][:100], p.returncode


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out: dict[str, dict[str, object]] = {}
    for name, text in REQUESTS.items():
        times, last, code = [], "", 0
        for _ in range(a.runs):
            dt, last, code = run_once(text)
            times.append(dt)
        out[name] = {"request": text, "median_s": round(statistics.median(times), 2), "min_s": round(min(times), 2),
                     "runs": [round(t, 2) for t in times], "exit": code, "last_line": last}
        print(f"{name:22s} median {out[name]['median_s']:6.2f}s  min {out[name]['min_s']:6.2f}s  exit {code}  | {last}")
    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import scar.cli.app"], check=True)
    out["import_cli_s"] = {"median_s": round(time.perf_counter() - t0, 2)}
    print(f"import scar.cli.app    {out['import_cli_s']['median_s']:.2f}s")
    if a.json:
        a.json.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
