"""Local model lifecycle (start on demand, idle unload, reuse existing server, admission), browser against a local
fixture server, dev-server readiness/crash, and CLI commands (D2)."""

from __future__ import annotations

import asyncio
import http.server
import socket
import sys
import textwrap
import threading
from pathlib import Path

import psutil
import pytest
from typer.testing import CliRunner

from scar.core.types import ToolStatus
from scar.providers.errors import ProviderError, ProviderErrorKind

FAKE_SERVER = textwrap.dedent('''
    import http.server, json, sys, time
    port = int(sys.argv[sys.argv.index("--port") + 1])
    time.sleep(0.5)
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "data": [{"id": "local"}]}).encode())
        def log_message(self, *a): pass
    print("fake llama-server args:", " ".join(sys.argv[1:]), flush=True)
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_llama(tmp_path: Path) -> Path:
    script = tmp_path / "fake_llama_server.py"
    script.write_text(FAKE_SERVER)
    launcher = tmp_path / "llama-server.cmd"
    launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    return launcher


def _admit_everything(services) -> None:  # type: ignore[no-untyped-def]
    """Lifecycle tests use a fake server; the machine's live GPU/RAM load must not decide whether it may start."""
    from scar.resources.admission import AdmissionDecision

    services.admission.local_inference = lambda size, purpose="llm", fallback=True: AdmissionDecision(
        True, "test: admitted", True, 99)


async def test_start_on_demand_and_idle_unload(services, fake_llama: Path, tmp_path: Path) -> None:
    _admit_everything(services)
    s = services.settings
    model = tmp_path / "model.gguf"
    model.write_bytes(b"\0" * 1024)
    s.local_llm_server_bin = str(fake_llama)
    s.local_llm_model_path = str(model)
    s.local_llm_url = f"http://127.0.0.1:{_free_port()}"
    s.local_inference_policy = "fallback_only"
    s.model_idle_timeout = 0.5
    lm = services.local_models
    url = await lm.ensure_llamacpp()
    st = lm._servers["llm"]
    assert st.owned and url.endswith("/v1") and st.process is not None
    pids = st.process.tree_pids()
    assert any(psutil.pid_exists(p) for p in pids)
    await asyncio.sleep(0.6)
    stopped = await lm.reap_idle()
    assert stopped == ["llm"]
    await asyncio.sleep(0.5)
    assert not any(psutil.pid_exists(p) and psutil.Process(p).status() != "zombie" for p in pids)
    assert "stopped llama-server (llm): idle" in " ".join(lm.events)


async def test_reuses_existing_server_and_never_kills_it(services) -> None:
    port = _free_port()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *a: object) -> None:
            return None

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        services.settings.local_llm_url = f"http://127.0.0.1:{port}"
        lm = services.local_models
        url = await lm.ensure_llamacpp()
        assert url == f"http://127.0.0.1:{port}/v1" and not lm._servers["llm"].owned
        assert await lm.stop_server("llm", "test") is False
        assert await lm.healthy(f"http://127.0.0.1:{port}")  # still up
    finally:
        srv.shutdown()


async def test_admission_refuses_local_inference(services) -> None:
    s = services.settings
    s.local_inference_policy = "never"
    assert not services.admission.local_inference(4000).admitted
    s.local_inference_policy = "fallback_only"
    assert not services.admission.local_inference(4000, fallback=False).admitted
    s.max_local_ram = 100
    s.max_local_vram = 100
    d = services.admission.local_inference(50_000)
    assert not d.admitted and "RAM" in d.reason


async def test_missing_model_is_unavailable_not_crash(services) -> None:
    services.settings.local_llm_url = f"http://127.0.0.1:{_free_port()}"
    services.settings.local_llm_model_path = ""
    with pytest.raises(ProviderError) as ei:
        await services.local_models.ensure_llamacpp()
    assert ei.value.kind == ProviderErrorKind.UNAVAILABLE


# ---------------------------------------------------------------- browser
PAGE = """<html><head><title>SCAR Fixture</title></head><body>
<h1>Token: PLAYWRIGHT-7Q2</h1><a href="/second">Next page</a>
<form action="/second"><label for="q">Query</label><input id="q" name="q"><button type="submit">Search</button></form>
</body></html>"""


@pytest.fixture
def site():  # type: ignore[no-untyped-def]
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = PAGE if self.path == "/" else "<html><title>Second</title><body><p>You reached page two</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a: object) -> None:
            return None

    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


async def test_browser_navigate_extract_type_click(runtime_parts, ctx_factory, site: str) -> None:
    from scar.tools.browser.manager import BrowserManager

    services = runtime_parts["services"]
    services.settings.browser_headless = True
    services.settings.browser_channel = "chromium"
    services.browser = BrowserManager(services.settings)
    p = runtime_parts["pipeline"]
    ctx = ctx_factory(f"open {site} and search for widgets")
    try:
        obs = await p.execute("browser.open", {"url": site}, ctx)
        assert obs.result.ok and obs.result.verification.verified is True, obs.result.summary
        obs = await p.execute("browser.extract", {"mode": "all_text"}, ctx)
        assert "PLAYWRIGHT-7Q2" in obs.result.data["text"] and "UNTRUSTED_DATA" in obs.result.model_view
        obs = await p.execute("browser.extract", {"mode": "snapshot"}, ctx)
        assert "Next page" in obs.result.data["text"]
        obs = await p.execute("browser.type", {"label": "Query", "value": "widgets"}, ctx)
        assert obs.result.ok and obs.result.verification.verified is True
        obs = await p.execute("browser.click", {"role": "link", "name": "Next page"}, ctx)
        assert obs.result.ok and obs.result.data["url"].endswith("/second")
        obs = await p.execute("browser.state", {}, ctx)
        assert obs.result.data["title"] == "Second"
    finally:
        await services.browser.close()


async def test_starting_one_model_evicts_scars_other_idle_model(services, fake_llama: Path, tmp_path: Path) -> None:
    _admit_everything(services)
    s = services.settings
    for name in ("llm.gguf", "vlm.gguf"):
        (tmp_path / name).write_bytes(bytes(1024))
    s.local_llm_server_bin = str(fake_llama)
    s.local_llm_model_path = str(tmp_path / "llm.gguf")
    s.local_vlm_model_path = str(tmp_path / "vlm.gguf")
    s.local_vlm_mmproj_path = ""
    s.local_llm_url = f"http://127.0.0.1:{_free_port()}"
    s.local_inference_policy = "fallback_only"
    lm = services.local_models
    await lm.ensure_llamacpp(vlm=True)
    with lm.serving("vlm"):
        await lm.ensure_llamacpp()  # the vision model is mid-request: it must not be evicted
        assert set(lm._servers) == {"llm", "vlm"}
    await lm.stop_server("llm")
    await lm.ensure_llamacpp()  # now idle: evicted so the LLM gets the VRAM
    assert set(lm._servers) == {"llm"}
    assert "stopped llama-server (vlm): making room for the llm model" in " ".join(lm.events)
    await lm.shutdown()


# ---------------------------------------------------------------- dev server
async def test_devserver_ready_and_crash(runtime_parts, ctx_factory, tmp_path: Path) -> None:
    port = _free_port()
    (tmp_path / "server.py").write_text(textwrap.dedent(f'''
        import http.server, time, sys
        time.sleep(1.0)
        print("Server ready at http://127.0.0.1:{port}", flush=True)
        http.server.HTTPServer(("127.0.0.1", {port}), http.server.SimpleHTTPRequestHandler).serve_forever()
    '''))
    p = runtime_parts["pipeline"]
    services = runtime_parts["services"]
    services.settings.allowed_roots = [str(tmp_path)]
    obs = await p.execute("devserver.start", {"path": str(tmp_path), "command": f'& "{sys.executable}" server.py',
                                              "wait_ready_s": 30}, ctx_factory(f"start the dev server in {tmp_path}"))
    assert obs.result.ok, obs.result.summary
    assert obs.result.verification.verified is True
    assert obs.result.data["http_status"] is not None and obs.result.data["url"].endswith(str(port))
    sid = obs.result.data["id"]
    ds = services.devservers.servers[sid]
    crashes = []
    services.bus.add_listener(lambda e: crashes.append(e) if e.kind == "monitor_fired" and
                              e.detail.get("kind") == "devserver_crashed" else None)
    for pid in ds.managed.tree_pids():
        if psutil.pid_exists(pid) and "python" in psutil.Process(pid).name().lower():
            psutil.Process(pid).kill()
    for _ in range(50):
        await asyncio.sleep(0.1)
        if crashes:
            break
    assert crashes and ds.status().startswith("crashed")



async def test_devserver_wrong_url_and_pattern_hints_do_not_hide_readiness(runtime_parts, ctx_factory, tmp_path: Path) -> None:
    port = _free_port()
    (tmp_path / "server.py").write_text(textwrap.dedent(f'''
        import http.server
        print("Server ready at http://127.0.0.1:{port}", flush=True)
        http.server.HTTPServer(("127.0.0.1", {port}), http.server.SimpleHTTPRequestHandler).serve_forever()
    '''))
    p = runtime_parts["pipeline"]
    services = runtime_parts["services"]
    services.settings.allowed_roots = [str(tmp_path)]
    obs = await p.execute("devserver.start", {"path": str(tmp_path), "command": f'& "{sys.executable}" server.py',
                                              "ready_pattern": "Server listening on", "url": f"http://localhost:{_free_port()}",
                                              "wait_ready_s": 30}, ctx_factory(f"start the dev server in {tmp_path}"))
    assert obs.result.ok, obs.result.summary
    assert obs.result.data["url"] == f"http://127.0.0.1:{port}" and obs.result.data["http_status"] is not None
    await services.devservers.stop_all()


# ---------------------------------------------------------------- CLI
def test_cli_commands(sandbox: Path) -> None:
    from scar.cli.app import _route_argv, app

    assert _route_argv(["open", "notepad"]) == ["run", "open", "notepad"]
    assert _route_argv(["-v", "open", "x"]) == ["-v", "run", "open", "x"]
    assert _route_argv(["config", "show"]) == ["config", "show"]
    r = CliRunner()
    res = r.invoke(app, ["config", "set", "autonomy_level", "2"])
    assert res.exit_code == 0, res.output
    res = r.invoke(app, ["config", "set", "groq_api_key", "x"])
    assert res.exit_code == 2
    res = r.invoke(app, ["config", "validate"])
    assert res.exit_code == 0 and "valid" in res.output
    res = r.invoke(app, ["permissions", "policy"])
    assert "disable_defender" in res.output
    res = r.invoke(app, ["memory", "list"])
    assert res.exit_code == 0
    res = r.invoke(app, ["tasks", "list"])
    assert res.exit_code == 0
    res = r.invoke(app, ["providers", "list"])
    assert "groq" in res.output and "reasoning" in res.output


def test_tool_unavailable_is_typed(runtime_parts) -> None:
    info = {i.name: i for i in runtime_parts["registry"].info()}
    assert info["read_artifact"].available
    assert ToolStatus.UNAVAILABLE.value == "unavailable"
