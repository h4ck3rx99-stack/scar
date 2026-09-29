/**
 * WebSocket link to the Python brain (contract: contracts/protocol.json, v2).
 * Reconnects forever with backoff; the brain may start after the body or restart.
 */
let ipcRenderer = null;
try {
  if (window.require) ipcRenderer = window.require('electron').ipcRenderer;
} catch {}

export class Bridge {
  constructor() {
    this.ws = null;
    this.handlers = new Map();
    this.connected = false;
    this.backoff = 250;
    this.seq = 0;
    this.config = null;
  }

  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, []);
    this.handlers.get(event).push(fn);
    return this;
  }

  emit(event, msg) {
    for (const fn of this.handlers.get(event) || []) {
      try { fn(msg); } catch (e) { console.error(`handler for ${event} failed`, e); }
    }
  }

  async loadConfig() {
    if (ipcRenderer) {
      this.config = await ipcRenderer.invoke('get-config');
    } else {
      // Plain browser (dev/testing): token comes from ?token=...
      const q = new URLSearchParams(location.search);
      this.config = { token: q.get('token') || 'dev', wsUrl: q.get('ws') || 'ws://127.0.0.1:8765', osTheme: 'dark', settings: {} };
    }
    return this.config;
  }

  async connect() {
    await this.loadConfig();
    if (!this.config.token) {
      // The brain writes the token on first start; try again shortly.
      return this._retry();
    }
    let ws;
    try {
      ws = new WebSocket(this.config.wsUrl);
    } catch {
      return this._retry();
    }
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 250;
      this.connected = true;
      const screens = [{ width: window.screen.width, height: window.screen.height, scale: window.devicePixelRatio }];
      this.send('hello', { token: this.config.token, screens });
      this.emit('_connected', {});
    };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      this.emit(msg.event, msg);
    };
    ws.onclose = () => {
      const was = this.connected;
      this.connected = false;
      this.ws = null;
      if (was) this.emit('_disconnected', {});
      this._retry();
    };
    ws.onerror = () => {};
  }

  _retry() {
    setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 5000);
  }

  send(event, payload = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ v: 2, id: `f${++this.seq}`, event, ...payload }));
    return true;
  }
}

export const bridge = new Bridge();
export { ipcRenderer };
