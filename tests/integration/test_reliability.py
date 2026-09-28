"""Failure injection (D6 reliability review): browser crash, database lock, network drop, microphone loss."""

from __future__ import annotations

import asyncio
import http.server
import socket
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import psutil
import pytest

from scar.core.types import ToolStatus


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.windows
async def test_browser_crash_is_detected_and_recovered(runtime_parts, ctx_factory) -> None:
    from scar.tools.browser.manager import BrowserManager

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><title>Crash test</title><body>alive</body></html>")

        def log_message(self, *a: object) -> None:
            return None

    port = _port()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    services = runtime_parts["services"]
    services.settings.browser_headless = True
    services.settings.browser_channel = "chromium"
    services.browser = BrowserManager(services.settings)
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"open http://127.0.0.1:{port}")
    try:
        obs = await p.execute("browser.open", {"url": f"http://127.0.0.1:{port}/"}, ctx)
        assert obs.result.ok
        # kill every chromium process the manager started (simulated crash)
        me = psutil.Process()
        for child in me.children(recursive=True):
            if "chrom" in child.name().lower() or "headless" in child.name().lower():
                child.kill()
        for _ in range(50):
            await asyncio.sleep(0.1)
            if services.browser.state.crashed:
                break
        assert services.browser.state.crashed
        obs = await p.execute("browser.state", {}, ctx)  # next call relaunches and restores the tab
        assert obs.result.ok and services.browser.state.crash_count >= 1
        assert any(f"127.0.0.1:{port}" in t["url"] for t in obs.result.data["tabs"])
    finally:
        await services.browser.close()
        srv.shutdown()


def test_database_lock_is_retried(tmp_path: Path) -> None:
    from scar.storage.db import Database

    db = Database(tmp_path / "lock.db")
    other = sqlite3.connect(str(tmp_path / "lock.db"), timeout=0.1, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN EXCLUSIVE")
    released = threading.Timer(0.6, lambda: other.execute("COMMIT"))
    released.start()
    t0 = time.monotonic()
    db.kv_set("k", {"v": 1})  # blocks on the exclusive lock, then succeeds via busy_timeout/backoff
    assert db.kv_get("k") == {"v": 1} and time.monotonic() - t0 >= 0.4
    released.join()
    other.close()
    db.close()


async def test_network_drop_during_fetch_is_an_observation(runtime_parts, ctx_factory, respx_mock) -> None:
    respx_mock.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://example.test/page").mock(side_effect=httpx.ConnectError("network is unreachable"))
    obs = await runtime_parts["pipeline"].execute("web.fetch", {"url": "https://example.test/page"}, ctx_factory("read example.test"))
    assert obs.result.status == ToolStatus.ERROR and "could not fetch" in obs.result.summary


async def test_search_falls_back_to_cache_when_offline(services) -> None:
    from scar.providers.base import SearchHit

    services.search._cache["python|3"] = (time.time(), "ddgs", [SearchHit("Python", "https://python.org")])
    services.settings.search_provider = "brave"

    async def offline(*a, **k):  # type: ignore[no-untyped-def]
        from scar.providers.errors import ProviderError, ProviderErrorKind

        raise ProviderError(ProviderErrorKind.TRANSIENT, "offline")

    services.search._search = offline  # every provider fails
    provider, hits = await services.search.search("python", 3)
    assert provider.startswith("cache:") and hits[0].url == "https://python.org"


async def test_microphone_loss_degrades_to_text(services) -> None:
    from scar.voice.session import VoiceSession

    class DeadMic:
        alive = False

        async def read(self, timeout: float = 1.0):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.01)
            return None

        def drain(self) -> None:
            return None

    vs = VoiceSession(services)
    vs.mic = DeadMic()  # type: ignore[assignment]
    vs.active = True
    with pytest.raises(RuntimeError, match="microphone disconnected"):
        await vs.listen_once(max_wait=1.0)


async def test_app_crash_detected_by_monitor(runtime_parts) -> None:
    import subprocess
    import sys

    services = runtime_parts["services"]
    proc = subprocess.Popen([sys.executable, "-c", "import os; os.abort()"])
    m = services.monitors.watch_process(proc.pid)
    for _ in range(60):
        await asyncio.sleep(0.1)
        if m.events:
            break
    assert m.events and m.events[0]["crashed"] and m.events[0]["exit_code"] != 0
