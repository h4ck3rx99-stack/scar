"""App API v1: HTTP + WebSocket on 127.0.0.1 for the desktop app (and any other local client).

Security (the API can approve actions, so it is a privileged surface):

* binds 127.0.0.1 only; the Host header must name that address and port (DNS-rebinding defence)
* a fresh 256-bit token per runtime start, written to ``<data>/app-api.json`` whose ACL grants only the current user;
  every request carries it (``Authorization: Bearer``), the event stream carries it as a WebSocket sub-protocol
* browsers: only the app's own origins are accepted (no wildcard CORS); requests with any other Origin are refused
* repeated authentication failures from a peer are rate-limited
* approvals must name a pending request and its exact args hash, and be marked as coming from a user gesture;
  CRITICAL ones also need the typed confirmation code (checked by the approval broker)
* secrets are write-only: the API reports whether a key is set, never its value
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from aiohttp import WSMsgType, web
from pydantic import BaseModel, ValidationError

from scar import __version__
from scar.api import models as m
from scar.api.humanize import CAPTURE_TOOLS, CONTROL_TOOLS, provider_summary, tool_title
from scar.core.events import AssistantState, ControlActive, Event, ScreenCaptured
from scar.runtime.ipc import restrict_to_user

log = structlog.get_logger("scar.api")

APP_ORIGINS = frozenset({"http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"})
DEV_ORIGINS = frozenset({"http://localhost:1420", "http://127.0.0.1:1420"})
AUTH_FAILURE_LIMIT = 10  # per peer per minute
ENDPOINT_FILE = "app-api.json"
WS_PROTOCOL = "scar.v1"


def endpoint_path(data_dir: Path) -> Path:
    return data_dir / ENDPOINT_FILE


def read_endpoint(data_dir: Path) -> dict[str, Any] | None:
    p = endpoint_path(data_dir)
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


class StateTracker:
    """Derives the one-word assistant state from runtime events and publishes changes."""

    def __init__(self, services: Any) -> None:
        self.s = services
        self.state = "ready"
        self._control_task: dict[str, str] = {}

    def compute(self) -> str:
        s = self.s
        voice = getattr(s, "voice", None)
        if voice is not None and getattr(voice, "speaking", False):
            return "speaking"
        if s.approvals.pending() or (s.questions is not None and bool(s.questions.pending())):
            return "waiting"
        tm = s.tasks
        if tm is not None and tm.running:
            return "working"
        if voice is not None and getattr(voice, "listening", False):
            return "listening"
        return "ready"

    def on_event(self, ev: Event) -> None:
        if ev.kind in ("assistant_state", "resource_snapshot", "mic_level", "assistant_delta"):
            return
        if ev.kind == "tool_called":
            tool = getattr(ev, "tool", "")
            if tool in CONTROL_TOOLS:
                self._control_task[getattr(ev, "action_id", "")] = tool
                self.s.bus.publish(ControlActive(task_id=ev.task_id, active=True, what=tool_title(tool)))
            if tool in CAPTURE_TOOLS:
                self.s.bus.publish(ScreenCaptured(task_id=ev.task_id))
        elif ev.kind == "tool_completed":
            if self._control_task.pop(getattr(ev, "action_id", ""), None) is not None and not self._control_task:
                self.s.bus.publish(ControlActive(task_id=ev.task_id, active=False))
        elif ev.kind in ("task_completed", "task_failed") and self._control_task:
            self._control_task.clear()
            self.s.bus.publish(ControlActive(task_id=ev.task_id, active=False))
        new = self.compute()
        if new != self.state:
            self.state = new
            self.s.bus.publish(AssistantState(state=new))  # type: ignore[arg-type]


class AppApi:
    def __init__(self, rt: Any, *, allow_dev_origin: bool | None = None) -> None:
        self.rt = rt
        self.s = rt.services
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        dev = os.environ.get("SCAR_APP_DEV") == "1" if allow_dev_origin is None else allow_dev_origin
        self.origins = APP_ORIGINS | (DEV_ORIGINS if dev else frozenset())
        self._failures: dict[str, deque[float]] = {}
        self._runner: web.AppRunner | None = None
        self._ws: set[web.WebSocketResponse] = set()
        self._seq = 0
        self.tracker = StateTracker(self.s)
        self._resource_subs = 0
        self._resource_task: asyncio.Task[None] | None = None
        self._auth_jobs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self, port: int = 0) -> int:
        app = web.Application(middlewares=[self._guard], client_max_size=2 * 1024 * 1024)
        self._routes(app)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port)
        await site.start()
        server = site._server
        assert server is not None
        self.port = int(server.sockets[0].getsockname()[1])  # type: ignore[union-attr]
        self.s.bus.add_listener(self.tracker.on_event)
        self._write_endpoint()
        log.info("app_api_started", port=self.port)
        return self.port

    async def stop(self) -> None:
        self.s.bus.remove_listener(self.tracker.on_event)
        for ws in list(self._ws):
            with contextlib.suppress(Exception):
                await ws.close(code=1001, message=b"runtime stopping")
        if self._resource_task is not None:
            self._resource_task.cancel()
        if self._runner is not None:
            await self._runner.cleanup()
        with contextlib.suppress(OSError):
            ep = read_endpoint(self.s.settings.data_path)
            if ep and ep.get("pid") == os.getpid():
                endpoint_path(self.s.settings.data_path).unlink()

    def _write_endpoint(self) -> None:
        p = endpoint_path(self.s.settings.data_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": f"http://127.0.0.1:{self.port}", "port": self.port, "token": self.token,
                                   "pid": os.getpid(), "api_version": m.API_VERSION, "app_version": __version__}),
                       encoding="utf-8")
        restrict_to_user(tmp)
        os.replace(tmp, p)

    # ------------------------------------------------------------------ security
    def _peer(self, request: web.Request) -> str:
        return request.remote or "?"

    def _too_many_failures(self, peer: str) -> bool:
        q = self._failures.get(peer)
        now = time.monotonic()
        if q is None:
            return False
        while q and now - q[0] > 60:
            q.popleft()
        return len(q) >= AUTH_FAILURE_LIMIT

    def _fail(self, peer: str) -> None:
        self._failures.setdefault(peer, deque(maxlen=100)).append(time.monotonic())

    def _token_ok(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "")
        supplied = auth[7:] if auth.startswith("Bearer ") else ""
        if not supplied and request.path == "/api/v1/events":
            protos = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
            supplied = next((p[5:] for p in protos if p.startswith("auth.")), "")
        return bool(supplied) and hmac.compare_digest(supplied.encode(), self.token.encode())

    def _cors(self, resp: web.StreamResponse, origin: str | None) -> None:
        if origin and origin in self.origins:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"

    @web.middleware
    async def _guard(self, request: web.Request, handler: Any) -> web.StreamResponse:
        peer = self._peer(request)
        if peer not in ("127.0.0.1", "::1"):
            return _err(403, "local connections only")
        host = request.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return _err(403, "bad host")
        origin = request.headers.get("Origin")
        if origin is not None and origin not in self.origins:
            return _err(403, "origin not allowed")
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
            self._cors(resp, origin)
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Max-Age"] = "600"
            return resp
        if self._too_many_failures(peer):
            return _err(429, "too many failed attempts; wait a minute")
        if request.path != "/api/v1/health" and not self._token_ok(request):
            self._fail(peer)
            return _err(401, "missing or wrong token")
        try:
            resp = await handler(request)
        except web.HTTPException:
            raise
        except ValidationError as exc:
            resp = _err(422, "invalid request: " + "; ".join(e["msg"] for e in exc.errors()[:3]))
        except ApiError as exc:
            resp = _err(exc.status, exc.message)
        except Exception as exc:
            log.exception("api_error", path=request.path)
            resp = _err(500, f"internal error ({type(exc).__name__}); see Diagnostics → Logs")
        self._cors(resp, origin)
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ------------------------------------------------------------------ routes
    def _routes(self, app: web.Application) -> None:
        r = app.router
        v = "/api/v1"
        r.add_get(f"{v}/health", self.health)
        r.add_get(f"{v}/hello", self.hello)
        r.add_get(f"{v}/events", self.events)
        r.add_get(f"{v}/status", self.status)
        r.add_get(f"{v}/snapshot", self.snapshot_route)
        r.add_post(f"{v}/tasks", self.submit)
        r.add_get(f"{v}/tasks", self.tasks)
        r.add_get(f"{v}/tasks/{{task_id}}", self.task_detail)
        r.add_post(f"{v}/tasks/{{task_id}}/cancel", self.cancel)
        r.add_post(f"{v}/stop-all", self.stop_all)
        r.add_get(f"{v}/approvals", self.approvals)
        r.add_post(f"{v}/approvals/{{request_id}}", self.answer_approval)
        r.add_post(f"{v}/questions/{{question_id}}", self.answer_question)
        r.add_get(f"{v}/grants", self.grants)
        r.add_delete(f"{v}/grants/{{grant_id}}", self.revoke_grant)
        r.add_delete(f"{v}/grants", self.revoke_all_grants)
        r.add_get(f"{v}/audit", self.audit)
        r.add_get(f"{v}/memory", self.memory)
        r.add_patch(f"{v}/memory/{{mid}}", self.memory_edit)
        r.add_delete(f"{v}/memory/{{mid}}", self.memory_forget)
        r.add_post(f"{v}/memory/wipe", self.memory_wipe)
        r.add_get(f"{v}/settings", self.settings_get)
        r.add_patch(f"{v}/settings", self.settings_patch)
        r.add_get(f"{v}/secrets", self.secrets_list)
        r.add_put(f"{v}/secrets/{{name}}", self.secret_put)
        r.add_delete(f"{v}/secrets/{{name}}", self.secret_delete)
        r.add_get(f"{v}/providers", self.providers)
        r.add_post(f"{v}/providers/{{provider}}/test", self.provider_test)
        r.add_get(f"{v}/accounts", self.accounts)
        r.add_post(f"{v}/accounts/{{name}}/connect", self.account_connect)
        r.add_post(f"{v}/accounts/{{name}}/disconnect", self.account_disconnect)
        r.add_get(f"{v}/doctor", self.doctor)
        r.add_get(f"{v}/logs", self.logs)
        r.add_get(f"{v}/monitors", self.monitors)
        r.add_post(f"{v}/monitors/{{mid}}/cancel", self.monitor_cancel)
        r.add_get(f"{v}/schedules", self.schedules)
        r.add_post(f"{v}/schedules/{{sid}}/cancel", self.schedule_cancel)
        r.add_post(f"{v}/voice", self.voice)
        r.add_post(f"{v}/diagnostics/bundle", self.diagnostics_bundle)
        r.add_post(f"{v}/shutdown", self.shutdown)

    # ------------------------------------------------------------------ basics
    async def health(self, _r: web.Request) -> web.Response:
        return web.json_response({"ok": True, "api_version": m.API_VERSION})

    async def hello(self, _r: web.Request) -> web.Response:
        return _json(m.Hello(api_version=m.API_VERSION, app_version=__version__, pid=os.getpid(),
                             data_dir=str(self.s.settings.data_path)))

    async def status(self, _r: web.Request) -> web.Response:
        from scar.cli.status import collect_status

        st = collect_status(self.s, self.s.tasks)
        st["provider"] = provider_summary(self.s)
        st["state"] = self.tracker.compute()
        return web.json_response(st, dumps=_dumps)

    def snapshot(self) -> m.Snapshot:
        tm = self.s.tasks
        running = [self._task_view(h.task) for h in (tm.running.values() if tm is not None else [])]
        from scar.runtime.gamemode import game_mode_active

        return m.Snapshot(state=self.tracker.compute(),  # type: ignore[arg-type]
                          provider=m.ProviderSummary(**provider_summary(self.s)),
                          pending_approvals=[self._approval_view(a) for a in self.s.approvals.pending()],
                          running_tasks=running, voice=_voice_state(self.s), game_mode=game_mode_active(),
                          killswitch_hotkey=self.s.settings.kill_switch_hotkey)

    async def snapshot_route(self, _r: web.Request) -> web.Response:
        return _json(self.snapshot())

    # ------------------------------------------------------------------ tasks
    async def submit(self, request: web.Request) -> web.Response:
        body = m.SubmitTask.model_validate(await _body(request))
        tm = self._tm()
        handle = await tm.submit(body.objective, origin=body.origin, background=body.background, autonomy=body.autonomy,
                                 dry_run=body.dry_run)
        return web.json_response({"task_id": handle.task.task_id})

    async def tasks(self, request: web.Request) -> web.Response:
        limit = min(int(request.query.get("limit", "30")), 200)
        rows = self.s.db.query(
            "SELECT task_id, objective, status, result_summary, state_json, created_at, finished_at, background, origin "
            "FROM tasks WHERE parent_task_id IS NULL ORDER BY created_at DESC LIMIT ?", (limit,))
        return web.json_response([self._row_view(r).model_dump() for r in rows])

    async def task_detail(self, request: web.Request) -> web.Response:
        tid = request.match_info["task_id"]
        row = self.s.db.query_one(
            "SELECT task_id, objective, status, result_summary, state_json, created_at, finished_at, background, origin "
            "FROM tasks WHERE task_id = ?", (tid,))
        if row is None:
            raise ApiError(404, "no such task")
        view = self._row_view(row)
        steps = [m.StepView(tool=c["tool"], title=tool_title(c["tool"]), status=c["status"] or "", summary=c["summary"] or "",
                            risk=c["risk"] or "", decision=c["decision"] or "", duration_ms=c["duration_ms"],
                            verified=None if c["verified"] is None else bool(c["verified"]))
                 for c in self.s.db.query("SELECT * FROM tool_calls WHERE task_id = ? ORDER BY created_at", (tid,))]
        return _json(m.TaskDetail(**view.model_dump(), steps=steps))

    async def cancel(self, request: web.Request) -> web.Response:
        ids = self._tm().cancel(request.match_info["task_id"])
        return web.json_response({"cancelled": ids})

    async def stop_all(self, _r: web.Request) -> web.Response:
        ran = self.s.killswitch.trigger("app")
        return web.json_response({"stopped": ran})

    # ------------------------------------------------------------------ approvals & questions
    async def approvals(self, _r: web.Request) -> web.Response:
        return web.json_response([self._approval_view(a).model_dump() for a in self.s.approvals.pending()])

    async def answer_approval(self, request: web.Request) -> web.Response:
        from scar.security.approval import ApprovalError, ApprovalResponse

        rid = request.match_info["request_id"]
        body = m.ApprovalAnswer.model_validate(await _body(request))
        req = self.s.approvals.get(rid)
        if req is None:
            raise ApiError(404, "that approval is no longer pending")
        if not hmac.compare_digest(body.args_hash, req.args_hash):
            raise ApiError(409, "the action changed since it was shown; review it again")
        if body.response != "deny" and not body.user_gesture:
            raise ApiError(403, "approvals must come from a click or key press on the approval card")
        try:
            res = self.s.approvals.resolve(rid, ApprovalResponse(body.response), "app",
                                           typed_confirmation=body.typed_confirmation)
        except ApprovalError as exc:
            raise ApiError(409, str(exc)) from exc
        return web.json_response({"ok": True, "response": res.response.value})

    async def answer_question(self, request: web.Request) -> web.Response:
        body = m.QuestionAnswer.model_validate(await _body(request))
        if self.s.questions is None or not self.s.questions.answer(request.match_info["question_id"], body.text):
            raise ApiError(404, "that question is no longer open")
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ permissions
    async def grants(self, _r: web.Request) -> web.Response:
        return web.json_response([g.model_dump(mode="json") for g in self.s.grants.list()], dumps=_dumps)

    async def revoke_grant(self, request: web.Request) -> web.Response:
        from scar.security.grants import UserAuthority

        ok = self.s.grants.revoke(request.match_info["grant_id"], UserAuthority("app", "revoked in the app"))
        if not ok:
            raise ApiError(404, "no such permission")
        return web.json_response({"ok": True})

    async def revoke_all_grants(self, _r: web.Request) -> web.Response:
        from scar.security.grants import UserAuthority

        return web.json_response({"revoked": self.s.grants.revoke_all(UserAuthority("app", "revoke all in the app"))})

    async def audit(self, request: web.Request) -> web.Response:
        limit = min(int(request.query.get("limit", "100")), 500)
        return web.json_response(self.s.audit.recent(limit, request.query.get("kind") or None), dumps=_dumps)

    # ------------------------------------------------------------------ memory
    def _mem(self) -> Any:
        if self.s.memory is None:
            raise ApiError(503, "memory is not available")
        return self.s.memory

    async def memory(self, request: web.Request) -> web.Response:
        mem = self._mem()
        q = request.query.get("q", "").strip()
        cat = request.query.get("category") or None
        items = mem.search(q, k=50, categories=[cat] if cat else None) if q else mem.list(cat, 300)
        return web.json_response([i.as_dict() for i in items], dumps=_dumps)

    async def memory_edit(self, request: web.Request) -> web.Response:
        body = m.MemoryEdit.model_validate(await _body(request))
        try:
            updated = self._mem().update(request.match_info["mid"], body.text)
        except KeyError as exc:
            raise ApiError(404, "no such memory") from exc
        except ValueError as exc:
            raise ApiError(422, str(exc)) from exc
        self.s.audit.append("memory_edited", {"id": updated.id, "by": "app"})
        return web.json_response(updated.as_dict(), dumps=_dumps)

    async def memory_forget(self, request: web.Request) -> web.Response:
        gone = self._mem().forget(request.match_info["mid"])
        if not gone:
            raise ApiError(404, "no such memory")
        self.s.audit.append("memory_forgotten", {"ids": gone, "by": "app"})
        return web.json_response({"forgotten": gone})

    async def memory_wipe(self, request: web.Request) -> web.Response:
        m.WipeMemory.model_validate(await _body(request))
        n = self._mem().wipe()
        self.s.audit.append("memory_wiped", {"count": n, "by": "app"})
        return web.json_response({"forgotten": n})

    # ------------------------------------------------------------------ settings & secrets
    async def settings_get(self, _r: web.Request) -> web.Response:
        from scar.api.settings_schema import settings_view

        return web.json_response(settings_view(self.s.settings), dumps=_dumps)

    async def settings_patch(self, request: web.Request) -> web.Response:
        from scar.config.writer import SettingError, set_setting

        body = m.SettingsPatch.model_validate(await _body(request))
        saved: dict[str, Any] = {}
        for key, value in body.values.items():
            try:
                field, parsed, _path = set_setting(key, value)
            except SettingError as exc:
                raise ApiError(422, f"{key}: {exc}") from exc
            setattr(self.s.settings, field, parsed)  # live: most settings are read on use
            saved[field] = parsed
        self.s.audit.append("settings_changed", {"fields": sorted(saved), "by": "app"})
        return web.json_response({"saved": saved}, dumps=_dumps)

    async def secrets_list(self, _r: web.Request) -> web.Response:
        from scar.config.settings import SECRET_KEYS

        out = [{"name": n, "set": self.s.secrets.has(n), "source": self.s.secrets.source_of(n) or ""} for n in sorted(SECRET_KEYS)]
        return web.json_response(out)

    async def secret_put(self, request: web.Request) -> web.Response:
        from scar.config.settings import SECRET_KEYS

        name = request.match_info["name"]
        if name not in SECRET_KEYS:
            raise ApiError(404, "unknown secret name")
        body = m.SecretValue.model_validate(await _body(request))
        self.s.secrets.set(name, body.value.strip())
        self.s.audit.append("secret_set", {"name": name, "by": "app"})
        return web.json_response({"name": name, "set": True})

    async def secret_delete(self, request: web.Request) -> web.Response:
        from scar.config.settings import SECRET_KEYS

        name = request.match_info["name"]
        if name not in SECRET_KEYS:
            raise ApiError(404, "unknown secret name")
        self.s.secrets.delete(name)
        self.s.audit.append("secret_deleted", {"name": name, "by": "app"})
        return web.json_response({"name": name, "set": False})

    # ------------------------------------------------------------------ providers & accounts
    async def providers(self, _r: web.Request) -> web.Response:
        router = self.s.router
        out = []
        for pid, spec in router.catalog.providers.items():
            ok, why = router.credential_status(spec)
            health = [h for h in self.s.health.all() if h.provider == pid]
            out.append({"id": pid, "tier": spec.tier, "local": spec.local, "configured": ok, "needs": "" if ok else why,
                        "key_names": list(spec.api_key_env), "data_terms": spec.data_terms, "setup_doc": spec.setup_doc,
                        "health": [{"model": h.model, "state": h.state, "last_error": h.last_error[:200]} for h in health]})
        return web.json_response({"providers": out, "summary": provider_summary(self.s),
                                  "last_route": router.last_route}, dumps=_dumps)

    async def provider_test(self, request: web.Request) -> web.Response:
        pid = request.match_info["provider"]
        if pid not in self.s.router.catalog.providers:
            raise ApiError(404, "unknown provider")
        result = await self.s.router.probe(pid)
        return web.json_response(result, dumps=_dumps)

    async def accounts(self, _r: web.Request) -> web.Response:
        from scar.api.accounts import account_status

        return web.json_response(account_status(self.s) | {"jobs": self._auth_jobs}, dumps=_dumps)

    async def account_connect(self, request: web.Request) -> web.Response:
        from scar.api.accounts import start_connect

        name = request.match_info["name"]
        job = await start_connect(self.s, name, self._auth_jobs)
        return web.json_response(job, dumps=_dumps)

    async def account_disconnect(self, request: web.Request) -> web.Response:
        from scar.api.accounts import disconnect

        return web.json_response(disconnect(self.s, request.match_info["name"]), dumps=_dumps)

    # ------------------------------------------------------------------ diagnostics
    async def doctor(self, _r: web.Request) -> web.Response:
        from scar.cli.doctor import Doctor

        results = await Doctor(self.s.settings, self.s).run()
        return web.json_response([{"ok": r.ok, "label": r.label, "hint": r.hint, "section": r.section} for r in results])

    async def logs(self, request: web.Request) -> web.Response:
        from scar.api.diagnostics import read_logs

        return web.json_response(read_logs(self.s.settings, task=request.query.get("task") or None,
                                           level=request.query.get("level") or None,
                                           limit=min(int(request.query.get("limit", "200")), 2000)), dumps=_dumps)

    async def diagnostics_bundle(self, _r: web.Request) -> web.Response:
        from scar.api.diagnostics import build_bundle

        path = await build_bundle(self.s)
        return web.json_response({"path": str(path)})

    # ------------------------------------------------------------------ monitors & schedules
    async def monitors(self, _r: web.Request) -> web.Response:
        rows = self.s.db.query("SELECT monitor_id, kind, status, target_json, created_at, last_event, task_id FROM monitors "
                               "ORDER BY created_at DESC LIMIT 100")
        return web.json_response(rows, dumps=_dumps)

    async def monitor_cancel(self, request: web.Request) -> web.Response:
        ok = self.s.monitors.cancel(request.match_info["mid"])
        if not ok:
            raise ApiError(404, "no such active monitor")
        return web.json_response({"ok": True})

    async def schedules(self, _r: web.Request) -> web.Response:
        rows = self.s.db.query("SELECT * FROM schedules ORDER BY (status = 'active') DESC, next_run LIMIT 200")
        return web.json_response(rows, dumps=_dumps)

    async def schedule_cancel(self, request: web.Request) -> web.Response:
        ok = self.s.scheduler.cancel(request.match_info["sid"])
        if not ok:
            raise ApiError(404, "no such active schedule")
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ voice
    async def voice(self, request: web.Request) -> web.Response:
        from scar.api.voice_control import voice_command

        body = m.VoiceCommand.model_validate(await _body(request))
        return web.json_response(await voice_command(self.s, body.action, body.mode), dumps=_dumps)

    async def shutdown(self, _r: web.Request) -> web.Response:
        stop = getattr(self.rt, "request_stop", None)
        if stop is None:
            raise ApiError(409, "this runtime cannot be stopped from the app")
        asyncio.get_running_loop().call_later(0.2, stop)
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ event stream
    async def events(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(protocols=(WS_PROTOCOL,), heartbeat=30, max_msg_size=64 * 1024)
        await ws.prepare(request)
        self._ws.add(ws)
        s = self.s
        s.approvals.attach_channel("app")
        if s.questions is not None:
            s.questions.attach_channel()
        topics: set[str] = set()
        try:
            async with s.bus.subscribe() as q:
                await ws.send_str(self._envelope("snapshot", snapshot=self.snapshot()))
                reader = asyncio.create_task(self._ws_reader(ws, topics))
                while not reader.done():
                    getter = asyncio.create_task(q.get())
                    done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                    if getter not in done:
                        getter.cancel()
                        break
                    ev: Event = getter.result()
                    if ev.kind == "resource_snapshot" and "resources" not in topics:
                        continue
                    if ev.kind == "mic_level" and "mic_level" not in topics:
                        continue
                    await ws.send_str(self._envelope("event", event=self._event_payload(ev)))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._ws.discard(ws)
            if "resources" in topics:
                self._resources(-1)
            s.approvals.detach_channel("app")
            if s.questions is not None:
                s.questions.detach_channel()
            with contextlib.suppress(Exception):
                await ws.close()
        return ws

    async def _ws_reader(self, ws: web.WebSocketResponse, topics: set[str]) -> None:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    return
                continue
            try:
                sub = m.Subscribe.model_validate_json(msg.data)
            except ValidationError:
                await ws.send_str(self._envelope("error", event={"message": "unsupported message"}))
                continue
            for t in sub.topics:
                if sub.op == "subscribe" and t not in topics:
                    topics.add(t)
                    if t == "resources":
                        self._resources(+1)
                elif sub.op == "unsubscribe" and t in topics:
                    topics.discard(t)
                    if t == "resources":
                        self._resources(-1)

    def _resources(self, delta: int) -> None:
        """Resource sampling runs only while some client shows it (no idle polling)."""
        self._resource_subs = max(0, self._resource_subs + delta)
        if self._resource_subs and self._resource_task is None:
            self._resource_task = asyncio.create_task(self._resource_loop(), name="app-api-resources")
        elif not self._resource_subs and self._resource_task is not None:
            self._resource_task.cancel()
            self._resource_task = None

    async def _resource_loop(self) -> None:
        from scar.api.diagnostics import resource_snapshot

        from scar.core.events import ResourceSnapshotEvent

        while True:
            snap = await asyncio.to_thread(resource_snapshot, self.s)
            self.s.bus.publish(ResourceSnapshotEvent(snapshot=snap))
            await asyncio.sleep(2.0)

    def _envelope(self, type_: str, *, event: dict[str, Any] | None = None, snapshot: m.Snapshot | None = None) -> str:
        self._seq += 1
        env = m.EventEnvelope(seq=self._seq, type=type_, event=event, snapshot=snapshot)  # type: ignore[arg-type]
        return env.model_dump_json()

    def _event_payload(self, ev: Event) -> dict[str, Any]:
        d = ev.model_dump(mode="json")
        if ev.kind == "approval_requested":
            req = self.s.approvals.get(getattr(ev, "request_id", ""))
            if req is not None:
                d["approval"] = self._approval_view(req).model_dump()
        elif ev.kind in ("tool_called", "tool_completed"):
            d["title"] = tool_title(getattr(ev, "tool", ""))
        elif ev.kind in ("provider_fallback", "provider_health"):
            d["provider"] = provider_summary(self.s)
        return d

    # ------------------------------------------------------------------ helpers
    def _tm(self) -> Any:
        if self.s.tasks is None:
            raise ApiError(503, "the runtime is still starting")
        return self.s.tasks

    def _approval_view(self, req: Any) -> m.ApprovalView:
        return m.ApprovalView(
            request_id=req.request_id, task_id=req.task_id, tool=req.tool, args_hash=req.args_hash, risk=req.risk.name,
            summary=req.summary, reason=req.reason, details=_redact_details(req.details), critical=req.critical,
            grantable=req.grantable, confirmation_code=req.confirmation_code if req.critical else "",
            allowed_responses=[r.value for r in req.allowed_responses()], created_at=req.created_at.isoformat(),
            expires_at=(req.created_at + timedelta(seconds=req.timeout_s)).isoformat())

    def _task_view(self, task: Any) -> m.TaskView:
        v = task.verification
        return m.TaskView(task_id=task.task_id, objective=task.objective, status=task.status.value,
                          result_summary=task.result_summary, verified=v.verified if v else None,
                          verification_note=(v.note if v else ""),
                          failed_checks=[c.name for c in v.checks if not c.passed] if v else [],
                          background=task.background, origin=task.origin.value)

    def _row_view(self, r: dict[str, Any]) -> m.TaskView:
        verified: bool | None = None
        note = ""
        failed: list[str] = []
        if r.get("state_json"):
            with contextlib.suppress(ValueError, TypeError, AttributeError):
                vj = json.loads(r["state_json"]).get("verification") or {}
                verified = vj.get("verified")
                note = vj.get("note") or ""
                failed = [c.get("name", "") for c in vj.get("checks") or [] if not c.get("passed")]
        return m.TaskView(task_id=r["task_id"], objective=r["objective"], status=r["status"],
                          result_summary=r["result_summary"] or "", verified=verified, verification_note=note,
                          failed_checks=failed, created_at=r.get("created_at"), finished_at=r.get("finished_at"),
                          background=bool(r.get("background")), origin=r.get("origin") or "text")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _redact_details(details: dict[str, Any]) -> dict[str, Any]:
    from scar.security.redaction import global_redactor

    return json.loads(global_redactor().redact(json.dumps(details, default=str)))


def _voice_state(services: Any) -> dict[str, Any]:
    v = getattr(services, "voice", None)
    if v is None:
        return {"state": "off", "mic_active": False, "muted": False, "mode": ""}
    return v.state_dict() if hasattr(v, "state_dict") else {"state": "idle", "mic_active": True, "muted": False, "mode": v.mode}


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _json(model: BaseModel) -> web.Response:
    return web.Response(text=model.model_dump_json(), content_type="application/json")


def _err(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _body(request: web.Request) -> Any:
    if request.content_type != "application/json":
        raise ApiError(415, "send JSON")
    try:
        return await request.json()
    except ValueError as exc:
        raise ApiError(400, "malformed JSON") from exc
