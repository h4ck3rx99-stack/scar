"""Phone remote: a small web page on the home network that sends commands to Scar.

Off until the user says "enable phone control". Then it listens on 0.0.0.0:8766, shows the address and a fresh
6-digit PIN, and only accepts WebSocket commands that carry that PIN. Five wrong PINs switch it off again.
Windows Firewall may ask once whether Python may accept connections on private networks.
"""
import asyncio
import base64
import io
import json
import logging
import secrets
import socket
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

log = logging.getLogger("phone")
PORT = 8766
MAX_BAD_PINS = 5


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scar remote</title><style>
:root{--bg:#070b10;--card:#0f1720;--line:#1f2d3a;--fg:#e8f1f7;--dim:#8aa0b2;--acc:#39e6ff}
*{box-sizing:border-box}body{margin:0;font:16px system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:520px;margin:auto;padding:16px}h1{font-size:20px;margin:6px 0 14px}h1 span{color:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin-bottom:12px}
input,button{font:inherit;border-radius:10px;border:1px solid var(--line);background:#0a1118;color:var(--fg);padding:12px}
input{width:100%}.row{display:flex;gap:8px}.row input{flex:1}
button{background:#12303c;border-color:#1d4b5c;cursor:pointer}button:active{transform:scale(.97)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.grid button{padding:12px 4px;font-size:14px}
#log{max-height:40vh;overflow:auto;font-size:14px}#log p{margin:6px 0}#log .me{color:var(--dim)}
img{width:100%;border-radius:10px;margin-top:8px;display:none}#pinbox{text-align:center}
</style></head><body><main>
<h1>🤖 <span>Scar</span> remote</h1>
<div class="card" id="pinbox"><p>Enter the PIN Scar is showing on your PC</p>
<div class="row"><input id="pin" inputmode="numeric" maxlength="6" placeholder="6-digit PIN"><button onclick="connect()">Connect</button></div></div>
<div id="app" style="display:none">
<div class="card"><div class="row"><input id="cmd" placeholder="Tell Scar anything…" onkeydown="if(event.key==='Enter')send()">
<button onclick="send()">Send</button><button id="mic" onclick="dictate()">🎤</button></div></div>
<div class="card grid">
<button onclick="q('how\\'s my battery')">🔋 Battery</button><button onclick="q('volume up')">🔊 Vol +</button><button onclick="q('volume down')">🔉 Vol −</button>
<button onclick="q('play')">⏯ Play/Pause</button><button onclick="q('next song')">⏭ Next</button><button onclick="q('mute')">🔇 Mute</button>
<button onclick="shot()">🖥 See screen</button><button onclick="q('go on standby')">😴 Standby</button><button onclick="q('lock my pc')">🔒 Lock PC</button>
</div>
<div class="card"><div id="log"></div><img id="img"></div></div>
</main><script>
let ws, pin;
const log = (t, me) => { const p = document.createElement('p'); p.textContent = t; if (me) p.className = 'me'; const l = document.getElementById('log'); l.appendChild(p); l.scrollTop = 1e9; };
function connect() {
  pin = document.getElementById('pin').value.trim();
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onopen = () => ws.send(JSON.stringify({ pin }));
  ws.onmessage = (e) => { const m = JSON.parse(e.data);
    if (m.ok) { document.getElementById('pinbox').style.display = 'none'; document.getElementById('app').style.display = 'block'; log('Connected to Scar.'); }
    if (m.error) { alert(m.error); }
    if (m.say) log('Scar: ' + m.say);
    if (m.image) { const i = document.getElementById('img'); i.src = 'data:image/jpeg;base64,' + m.image; i.style.display = 'block'; } };
  ws.onclose = () => log('Disconnected.');
}
function q(text) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify({ pin, text })); log('You: ' + text, true); } }
function send() { const i = document.getElementById('cmd'); if (i.value.trim()) { q(i.value.trim()); i.value = ''; } }
function shot() { if (ws) ws.send(JSON.stringify({ pin, screenshot: true })); }
function dictate() { const R = window.SpeechRecognition || window.webkitSpeechRecognition; if (!R) { alert('Voice typing is not supported in this browser; use the keyboard mic instead.'); return; }
  const r = new R(); r.lang = 'en-IN'; r.onresult = (e) => { document.getElementById('cmd').value = e.results[0][0].transcript; send(); }; r.start(); }
</script></body></html>"""


class PhoneRemote:
    def __init__(self, session):
        self.session = session
        self.server = None
        self.pin = None
        self.bad = 0
        self.clients = set()
        session.ui.listeners.append(self._on_ui_message)

    @property
    def running(self):
        return self.server is not None

    async def start(self):
        if not self.running:
            self.pin = f"{secrets.randbelow(10**6):06d}"
            self.bad = 0
            self.server = await serve(self._conn, "0.0.0.0", PORT, process_request=self._http, max_size=2**16)
            log.info("phone remote on http://%s:%d", lan_ip(), PORT)
        return f"http://{lan_ip()}:{PORT}", self.pin

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.server, self.pin, self.clients = None, None, set()

    def _http(self, connection, request):
        if request.path.split("?")[0] == "/ws":
            return None
        body = PAGE.encode()
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body)),
                                            "Cache-Control": "no-store"}), body)

    async def _conn(self, ws):
        authed = False
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not authed:
                    if msg.get("pin") and secrets.compare_digest(str(msg["pin"]), self.pin or ""):
                        authed = True
                        self.clients.add(ws)
                        await ws.send(json.dumps({"ok": True}))
                        log.info("phone connected from %s", ws.remote_address[0])
                    else:
                        self.bad += 1
                        await ws.send(json.dumps({"error": "Wrong PIN"}))
                        if self.bad >= MAX_BAD_PINS:
                            log.warning("too many wrong PINs: switching phone remote off")
                            asyncio.get_running_loop().create_task(self.stop())
                        return
                    continue
                if msg.get("screenshot"):
                    await ws.send(json.dumps({"image": await asyncio.get_running_loop().run_in_executor(None, _screenshot_b64)}))
                elif msg.get("text"):
                    await self.session.text_command(str(msg["text"])[:500])
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _on_ui_message(self, msg):
        """Mirror Scar's spoken replies to connected phones."""
        if msg.get("event") == "say" and self.clients:
            data = json.dumps({"say": msg["text"]})
            for ws in list(self.clients):
                try:
                    await ws.send(data)
                except ConnectionClosed:
                    self.clients.discard(ws)


def _screenshot_b64():
    from PIL import ImageGrab
    img = ImageGrab.grab()
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()
