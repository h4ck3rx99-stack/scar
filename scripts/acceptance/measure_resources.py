"""Resource acceptance (Part D4). Measures idle CPU/RSS, GPU release after idle timeout, orphan processes,
absence of continuous activity, bounded growth, and cloud-first routing. Writes docs/resource_results.json.

    uv run python scripts/acceptance/measure_resources.py [--idle-seconds 60]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scar.config.settings import load_settings  # noqa: E402
from scar.core.events import EventBus, TaskProgress  # noqa: E402
from scar.providers.base import ChatMessage, ChatRequest  # noqa: E402
from scar.providers.capabilities import ordered_candidates  # noqa: E402
from scar.runtime.runtime import Runtime  # noqa: E402


def gpu_processes() -> list[dict[str, Any]]:
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        procs = pynvml.nvmlDeviceGetComputeRunningProcesses(h) + pynvml.nvmlDeviceGetGraphicsRunningProcesses(h)
        out = []
        for p in procs:
            try:
                name = psutil.Process(p.pid).name()
            except psutil.Error:
                name = "?"
            out.append({"pid": p.pid, "name": name, "used_mb": (p.usedGpuMemory or 0) / 2**20})
        return out
    except Exception as exc:
        return [{"error": str(exc)}]


def vram_used_mb() -> float | None:
    try:
        import pynvml

        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0)).used / 2**20
    except Exception:
        return None


async def main(idle_seconds: int) -> int:
    results: dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    settings = load_settings(model_idle_timeout=20.0)
    rt = Runtime(settings)
    await rt.start(with_hotkeys=True)
    me = psutil.Process()
    try:
        # ---- 1. idle CPU / RSS
        await asyncio.sleep(5)
        samples_before = rt.services.monitor.samples_taken
        artifacts_before = sum(1 for _ in settings.artifacts_path.rglob("*"))
        me.cpu_percent(None)
        cpu_samples = []
        for _ in range(idle_seconds):
            await asyncio.sleep(1)
            cpu_samples.append(me.cpu_percent(None) / psutil.cpu_count())
        results["idle"] = {
            "seconds": idle_seconds,
            "avg_cpu_percent_of_machine": round(sum(cpu_samples) / len(cpu_samples), 3),
            "max_cpu_percent_of_machine": round(max(cpu_samples), 3),
            "rss_mb": round(me.memory_info().rss / 2**20, 1),
            "threads": me.num_threads(),
            "children": [c.name() for c in me.children(recursive=True)],
            "resource_samples_taken_while_idle": rt.services.monitor.samples_taken - samples_before,
            "artifacts_created_while_idle": sum(1 for _ in settings.artifacts_path.rglob("*")) - artifacts_before,
            "stt_loaded": rt.services.stt.local_whisper.loaded,
            "embeddings_loaded": rt.services.embeddings.loaded,
            "voice_active": rt.services.voice is not None,
        }
        results["idle"]["pass_cpu_below_2pct"] = results["idle"]["avg_cpu_percent_of_machine"] < 2.0

        # ---- 2. GPU released after idle timeout
        vram0 = vram_used_mb()
        local = rt.services.local_models
        if settings.local_llm_model_path:
            t0 = time.monotonic()
            url = await local.ensure_llamacpp()
            from scar.providers.llm.openai_compat import OpenAICompatClient

            c = OpenAICompatClient("llamacpp", url, None)
            resp = await c.chat("local", ChatRequest(messages=[ChatMessage(role="user", content="Say OK.")], max_tokens=8))
            await c.aclose()
            local.touch("llm")
            loaded_ms = round((time.monotonic() - t0) * 1000)
            vram_loaded = vram_used_mb()
            server_pids = local._servers["llm"].process.tree_pids() if local._servers.get("llm") and local._servers["llm"].process else []
            await asyncio.sleep(settings.model_idle_timeout + 10)
            alive = [p for p in server_pids if psutil.pid_exists(p)]
            vram_after = vram_used_mb()
            results["gpu"] = {"vram_mb_before": vram0, "vram_mb_while_loaded": vram_loaded, "vram_mb_after_idle": vram_after,
                              "load_and_first_reply_ms": loaded_ms, "reply": resp.content[:20],
                              "server_pids": server_pids, "server_pids_alive_after_idle": alive,
                              "lifecycle_events": local.events[-4:], "gpu_processes_after": gpu_processes(),
                              "pass_released": not alive and not local._servers}
        else:
            results["gpu"] = {"skipped": "SCAR_LOCAL_LLM_MODEL_PATH not set"}

        # ---- 3. orphans after tasks complete / are cancelled
        tm = rt.tasks
        assert tm is not None
        pipeline = rt.pipeline
        assert pipeline is not None
        from scar.core.types import TaskState

        ctx = tm._ctx(TaskState(objective="run commands"), tm.root_cancel.child(), False)
        await pipeline.execute("terminal.exec", {"argv": [sys.executable, "-c", "print('done')"]}, ctx)
        long_task = asyncio.create_task(pipeline.execute("terminal.run", {"command": "Start-Process -NoNewWindow ping -ArgumentList '-n','60','127.0.0.1'; Start-Sleep 60", "timeout_s": 90}, ctx))
        await asyncio.sleep(3)
        ctx.cancel.cancel("measure")
        await long_task
        await asyncio.sleep(1)
        helper_pids: set[int] = set()
        parser = rt.services.command_guard.ps_parser
        if parser is not None and parser._proc is not None:  # SCAR's own parse-only helper (idle-reaped after 5 min)
            hp = psutil.Process(parser._proc.pid)
            helper_pids = {hp.pid, *(c.pid for c in hp.children(recursive=True))}
        kids = [f"{c.name()}#{c.pid}" for c in me.children(recursive=True) if c.pid not in helper_pids]
        results["orphans"] = {"children_after_complete_and_cancel": kids, "scar_helper_pids": sorted(helper_pids),
                              "managed": [m.pid for m in rt.services.processes.list()],
                              "pass_no_orphans": not kids}

        # ---- 4. bounded growth
        bus = EventBus(queue_size=256)
        async with bus.subscribe() as q:
            for i in range(100_000):
                bus.publish(TaskProgress(message=str(i)))
            qsize = q.qsize()
        from scar.tools.web.tools import _STATE

        results["bounded"] = {"event_queue_after_100k": qsize, "fetch_cache_entries": len(_STATE.cache),
                              "search_cache_entries": len(rt.services.search._cache),
                              "artifact_retention_bytes_cap": rt.services.artifacts.max_total_bytes,
                              "pass_bounded": qsize <= 256}

        # ---- 5. cloud-first routing
        order = [c.provider for c in ordered_candidates(rt.services.router.catalog, "reasoning", settings)]
        results["routing"] = {"reasoning_order": order, "local_after_cloud": order.index("ollama") > order.index("groq"),
                              "cloud_available_now": rt.services.router.cloud_available("reasoning"),
                              "local_inference_policy": settings.local_inference_policy}
    finally:
        await rt.stop()
    await asyncio.sleep(1)
    results["after_shutdown"] = {"children": [c.name() for c in psutil.Process().children(recursive=True)],
                                 "llama_server_running": any((p.info["name"] or "").lower().startswith("llama-server")
                                                             for p in psutil.process_iter(["name"]))}
    out = ROOT / "docs" / "resource_results.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-seconds", type=int, default=60)
    raise SystemExit(asyncio.run(main(ap.parse_args().idle_seconds)))
