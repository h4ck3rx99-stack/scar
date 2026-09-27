"""App API v1: contract and security. The API can approve actions, so every guard is tested here."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scar.api.server import APP_ORIGINS, AUTH_FAILURE_LIMIT, AppApi, read_endpoint


@pytest.fixture
async def api(runtime_parts):  # type: ignore[no-untyped-def]
    from scar.agent.runner import TaskManager

    services = runtime_parts["services"]
    services.tasks = TaskManager(services, runtime_parts["registry"], runtime_parts["pipeline"])
    services.router.cloud_available = lambda category: False  # type: ignore[method-assign]
    rt = SimpleNamespace(services=services, tasks=services.tasks, stopped=False)
    rt.request_stop = lambda: setattr(rt, "stopped", True)
    server = AppApi(rt, allow_dev_origin=False)
    await server.start()
    yield server
    await server.stop()


def _client(api: AppApi, token: str | None = "ok", **headers: str) -> httpx.AsyncClient:
    h = dict(headers)
    if token == "ok":
        h["Authorization"] = f"Bearer {api.token}"
    elif token:
        h["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{api.port}", headers=h, timeout=20)


async def test_endpoint_file_carries_url_and_token(api: AppApi) -> None:
    ep = read_endpoint(api.s.settings.data_path)
    assert ep is not None and ep["port"] == api.port and ep["token"] == api.token and ep["api_version"] == "1"


async def test_token_required_everywhere_but_health(api: AppApi) -> None:
    async with _client(api, token=None) as c:
        assert (await c.get("/api/v1/health")).status_code == 200
        assert (await c.get("/api/v1/hello")).status_code == 401
    async with _client(api, token="wrong-token") as c:
        assert (await c.get("/api/v1/hello")).status_code == 401
        assert (await c.post("/api/v1/stop-all")).status_code == 401
    async with _client(api) as c:
        r = await c.get("/api/v1/hello")
        assert r.status_code == 200 and r.json()["api_version"] == "1"


async def test_auth_failures_are_rate_limited(api: AppApi) -> None:
    async with _client(api, token="nope") as bad:
        for _ in range(AUTH_FAILURE_LIMIT):
            await bad.get("/api/v1/hello")
    async with _client(api) as good:
        assert (await good.get("/api/v1/hello")).status_code == 429  # even the right token waits after a burst


async def test_host_and_origin_checks_block_rebinding_and_csrf(api: AppApi) -> None:
    async with _client(api, Host="evil.example") as c:
        assert (await c.get("/api/v1/hello")).status_code == 403
    async with _client(api, Origin="https://evil.example") as c:
        assert (await c.get("/api/v1/hello")).status_code == 403
        assert (await c.post("/api/v1/stop-all")).status_code == 403
    origin = sorted(APP_ORIGINS)[0]
    async with _client(api, Origin=origin) as c:
        r = await c.get("/api/v1/hello")
        assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == origin
        pre = await c.options("/api/v1/tasks")
        assert pre.status_code == 204 and pre.headers["Access-Control-Allow-Origin"] == origin
        assert "*" not in pre.headers.get("Access-Control-Allow-Origin", "")


async def test_dev_origin_only_in_dev_mode(api: AppApi) -> None:
    async with _client(api, Origin="http://localhost:1420") as c:
        assert (await c.get("/api/v1/hello")).status_code == 403


async def test_submit_task_and_read_it_back(api: AppApi) -> None:
    async with _client(api) as c:
        r = await c.post("/api/v1/tasks", json={"objective": "remember that the api test colour is amber"})
        assert r.status_code == 200
        tid = r.json()["task_id"]
        for _ in range(100):
            detail = (await c.get(f"/api/v1/tasks/{tid}")).json()
            if detail["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.05)
        assert detail["status"] == "succeeded" and detail["steps"][0]["title"] == "Remembering"
        listed = (await c.get("/api/v1/tasks")).json()
        assert any(t["task_id"] == tid for t in listed)
        bad = await c.post("/api/v1/tasks", json={"objective": "", "extra": 1})
        assert bad.status_code == 422


async def _pending_approval(api: AppApi, runtime_parts: dict[str, Any], ctx_factory: Any, sandbox: Path) -> tuple[Any, Any]:
    """A HIGH-risk action waiting for the app's decision (moving a file)."""
    (sandbox / "a.txt").write_text("x")
    ctx = ctx_factory(f"move a.txt to b.txt in {sandbox}")
    api.s.approvals.attach_channel("app")
    job = asyncio.create_task(runtime_parts["pipeline"].execute(
        "fs.move", {"src": str(sandbox / "a.txt"), "dst": str(sandbox / "b.txt")}, ctx))
    for _ in range(100):
        if api.s.approvals.pending():
            break
        await asyncio.sleep(0.02)
    return api.s.approvals.pending()[0], job


async def test_approval_binding_hash_gesture_and_replay(api: AppApi, runtime_parts, ctx_factory, sandbox: Path) -> None:
    req, job = await _pending_approval(api, runtime_parts, ctx_factory, sandbox)
    url = f"/api/v1/approvals/{req.request_id}"
    async with _client(api) as c:
        view = (await c.get("/api/v1/approvals")).json()[0]
        assert view["args_hash"] == req.args_hash and view["risk"] == "HIGH" and view["expires_at"]
        wrong = await c.post(url, json={"response": "allow_once", "args_hash": "0" * 64, "user_gesture": True})
        assert wrong.status_code == 409
        no_gesture = await c.post(url, json={"response": "allow_once", "args_hash": req.args_hash})
        assert no_gesture.status_code == 403
        ok = await c.post(url, json={"response": "allow_once", "args_hash": req.args_hash, "user_gesture": True})
        assert ok.status_code == 200
        replay = await c.post(url, json={"response": "allow_once", "args_hash": req.args_hash, "user_gesture": True})
        assert replay.status_code == 404
    obs = await job
    assert obs.result.ok and (sandbox / "b.txt").exists()


async def test_deny_from_the_app(api: AppApi, runtime_parts, ctx_factory, sandbox: Path) -> None:
    req, job = await _pending_approval(api, runtime_parts, ctx_factory, sandbox)
    async with _client(api) as c:
        r = await c.post(f"/api/v1/approvals/{req.request_id}", json={"response": "deny", "args_hash": req.args_hash})
        assert r.status_code == 200
    obs = await job
    assert not obs.result.ok and (sandbox / "a.txt").exists()


async def test_event_stream_auth_origin_and_snapshot(api: AppApi) -> None:
    import aiohttp

    url = f"http://127.0.0.1:{api.port}/api/v1/events"
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.WSServerHandshakeError) as e1:
            await session.ws_connect(url, protocols=("scar.v1", "auth.wrong"))
        assert e1.value.status == 401
        with pytest.raises(aiohttp.WSServerHandshakeError) as e2:
            await session.ws_connect(url, protocols=("scar.v1", f"auth.{api.token}"), origin="https://evil.example")
        assert e2.value.status == 403
        async with session.ws_connect(url, protocols=("scar.v1", f"auth.{api.token}"), origin=sorted(APP_ORIGINS)[0]) as ws:
            first = json.loads((await ws.receive(timeout=5)).data)
            assert first["type"] == "snapshot" and first["snapshot"]["state"] in ("ready", "working")
            assert "app" in api.s.approvals.channels
            from scar.core.events import TaskProgress

            api.s.bus.publish(TaskProgress(message="hello from the runtime"))
            for _ in range(20):
                msg = json.loads((await ws.receive(timeout=5)).data)
                if msg["type"] == "event" and msg["event"]["kind"] == "task_progress":
                    assert msg["event"]["message"] == "hello from the runtime" and msg["seq"] > first["seq"]
                    break
            else:
                raise AssertionError("event not delivered")
            await ws.send_str(json.dumps({"op": "bogus"}))
            err = json.loads((await ws.receive(timeout=5)).data)
            assert err["type"] == "error"
    await asyncio.sleep(0.1)
    assert "app" not in api.s.approvals.channels


async def test_secrets_are_write_only(api: AppApi) -> None:
    async with _client(api) as c:
        r = await c.put("/api/v1/secrets/GROQ_API_KEY", json={"value": "gsk_" + "x" * 40})
        assert r.status_code == 200 and "gsk_" not in r.text
        listed = await c.get("/api/v1/secrets")
        entry = next(s for s in listed.json() if s["name"] == "GROQ_API_KEY")
        assert entry["set"] is True and "gsk_" not in listed.text
        assert (await c.put("/api/v1/secrets/NOT_A_KEY", json={"value": "x"})).status_code == 404


async def test_settings_patch_validates_persists_and_applies(api: AppApi) -> None:
    async with _client(api) as c:
        r = await c.patch("/api/v1/settings", json={"values": {"autonomy_level": 2, "theme": "dark"}})
        assert r.status_code == 200 and api.s.settings.autonomy_level == 2 and api.s.settings.theme == "dark"
        assert (await c.patch("/api/v1/settings", json={"values": {"autonomy_level": 9}})).status_code == 422
        assert (await c.patch("/api/v1/settings", json={"values": {"GROQ_API_KEY": "x"}})).status_code == 422
        fields = {f["name"]: f for f in (await c.get("/api/v1/settings")).json()["fields"]}
        assert fields["theme"]["value"] == "dark" and fields["theme"]["choices"] == ["system", "light", "dark"]
        assert "groq_api_key" not in fields


async def test_memory_edit_forget_and_guarded_wipe(api: AppApi) -> None:
    mid, _ = api.s.memory.store("my favourite editor is VS Code", "preference")
    async with _client(api) as c:
        r = await c.patch(f"/api/v1/memory/{mid}", json={"text": "my favourite editor is Neovim"})
        assert r.status_code == 200 and api.s.memory.get(mid).text.endswith("Neovim")
        refused = await c.patch(f"/api/v1/memory/{mid}", json={"text": "my password is hunter2"})
        assert refused.status_code == 422  # the memory write policy still applies
        assert (await c.post("/api/v1/memory/wipe", json={"confirm": "yes"})).status_code == 422
        assert (await c.delete(f"/api/v1/memory/{mid}")).status_code == 200 and api.s.memory.get(mid) is None


async def test_status_accounts_doctor_and_errors_are_readable(api: AppApi) -> None:
    async with _client(api) as c:
        st = (await c.get("/api/v1/status")).json()
        assert st["provider"]["summary"] and st["state"] == "ready"
        acc = (await c.get("/api/v1/accounts")).json()["accounts"]
        google = next(a for a in acc if a["name"] == "google")
        assert google["connected"] is False and google["needs"]
        missing = await c.get("/api/v1/tasks/task_does_not_exist")
        assert missing.status_code == 404 and missing.json() == {"error": "no such task"}


async def test_shutdown_asks_the_runtime_to_stop(api: AppApi) -> None:
    async with _client(api) as c:
        assert (await c.post("/api/v1/shutdown")).status_code == 200
    await asyncio.sleep(0.4)
    assert api.rt.stopped is True
