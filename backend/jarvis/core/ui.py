"""WebSocket link to the Electron body (contract v2, see contracts/protocol.json).

Also serves window captures over plain HTTP on the same port: GET /cap/<name>.webp
"""
import asyncio
import json
import logging
import uuid
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from jarvis import config

log = logging.getLogger("ui")
PROTOCOL_VERSION = 2


class UiLink:
    def __init__(self):
        self.token = config.session_token()
        self.clients = set()          # authenticated connections
        self.handlers = {}            # event name -> async fn(msg)
        self._pending = {}            # fly_to id -> Future
        self.proxy_waiters = {}       # (hwnd, "ready"|"arrived") -> Future for the cloth card handshake
        self.screens = []
        self.connected = asyncio.Event()

    def on(self, event):
        def deco(fn):
            self.handlers[event] = fn
            return fn
        return deco

    # ------------------------------------------------------------------ send
    async def send(self, event, **payload):
        msg = {"v": PROTOCOL_VERSION, "id": payload.pop("id", None) or uuid.uuid4().hex[:12], "event": event, **payload}
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send(data)
            except Exception:  # noqa: BLE001 - dead socket; the connection handler cleans up
                self.clients.discard(ws)
        return msg["id"]

    def emit(self, event, **payload):
        """Fire-and-forget send from sync code."""
        asyncio.get_running_loop().create_task(self.send(event, **payload))

    async def fly_to(self, x, y, purpose, duration_ms=650, button=None):
        """Ask the avatar to fly to physical screen pixel (x, y). Returns when it arrives, or after a
        timeout. The OS action never depends on the animation, it only waits for it."""
        if not self.clients:
            return False
        mid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        extra = {"button": button} if button else {}
        await self.send("fly_to", id=mid, target={"x": int(x), "y": int(y)}, purpose=purpose, duration_ms=duration_ms, **extra)
        try:
            await asyncio.wait_for(fut, duration_ms / 1000 + 0.6 + config.DESTINATION_TIMEOUT_S)
            return True
        except asyncio.TimeoutError:
            log.warning("destination_reached timeout for %s", purpose)
            return False
        finally:
            self._pending.pop(mid, None)

    # --------------------------------------------------------------- receive
    def _process_request(self, connection, request):
        path = request.path.split("?")[0]
        if path.startswith("/cap/"):
            name = path[5:]
            f = config.CAPTURE_DIR / name
            if "/" in name or "\\" in name or ".." in name or not f.is_file():
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
            body = f.read_bytes()
            headers = Headers({"Content-Type": "image/webp", "Content-Length": str(len(body)),
                               "Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})
            return Response(200, "OK", headers, body)
        origin = request.headers.get("Origin")
        # Any web page in any browser can open ws://127.0.0.1 - only the Electron app may connect.
        if origin is not None and origin not in config.ALLOWED_ORIGINS:
            log.warning("rejected websocket from origin %s", origin)
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden origin\n")
        return None

    async def _conn(self, ws):
        authed = False
        dev_ok = config.DEV_NO_TOKEN and ws.request.headers.get("Origin") in config.DEV_ORIGINS
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                ev = msg.get("event")
                if not authed:
                    if ev == "hello" and (msg.get("token") == self.token or dev_ok):
                        authed = True
                        self.clients.add(ws)
                        self.screens = msg.get("screens", [])
                        self.connected.set()
                        log.info("ui connected (%d screens)", len(self.screens))
                        if "hello" in self.handlers:
                            await self.handlers["hello"](msg)
                    else:
                        await ws.close(4001, "bad token")
                        return
                    continue
                if ev == "destination_reached":
                    fut = self._pending.get(msg.get("reply_to"))
                    if fut and not fut.done():
                        fut.set_result(True)
                    continue
                if ev in ("proxy_ready", "proxy_arrived"):
                    try:
                        key = (int(msg.get("window_id", "0"), 16), ev.split("_")[1])
                    except ValueError:
                        continue
                    fut = self.proxy_waiters.get(key)
                    if fut and not fut.done():
                        fut.set_result(True)
                    continue
                handler = self.handlers.get(ev)
                if handler:
                    asyncio.get_running_loop().create_task(handler(msg))
        finally:
            self.clients.discard(ws)
            if not self.clients:
                self.connected.clear()
            if authed:
                log.info("ui disconnected")

    async def serve(self):
        config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        return await serve(self._conn, config.WS_HOST, config.WS_PORT,
                           process_request=self._process_request, max_size=2**20)
