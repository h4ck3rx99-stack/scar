// Client for the runtime's local App API (v1). No polling: state arrives on the event stream.
import type { EventEnvelope } from "./types";

export interface Endpoint {
  url: string; // http://127.0.0.1:PORT
  token: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Human-readable, next-step-oriented message for any failure (never a stack trace). */
export function humanError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "The app lost its connection key. Restart SCAR from the tray.";
    if (e.status === 429) return "Too many attempts. Wait a minute and try again.";
    if (e.status === 0) return "SCAR's runtime isn't reachable. It may still be starting.";
    return e.message.charAt(0).toUpperCase() + e.message.slice(1);
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

export class ApiClient {
  constructor(public readonly ep: Endpoint) {}

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.ep.url}/api/v1${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${this.ep.token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, "SCAR's runtime isn't reachable");
    }
    const text = await res.text();
    const data = text ? (JSON.parse(text) as unknown) : null;
    if (!res.ok) {
      const msg = (data as { error?: string } | null)?.error ?? `request failed (${res.status})`;
      throw new ApiError(res.status, msg);
    }
    return data as T;
  }

  get<T>(path: string) {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body: unknown = {}) {
    return this.request<T>("POST", path, body);
  }
  patch<T>(path: string, body: unknown) {
    return this.request<T>("PATCH", path, body);
  }
  put<T>(path: string, body: unknown) {
    return this.request<T>("PUT", path, body);
  }
  del<T>(path: string) {
    return this.request<T>("DELETE", path);
  }
}

export type StreamStatus = "connecting" | "online" | "offline";

/** WebSocket event stream with bounded exponential reconnect. The token travels as a sub-protocol (browsers cannot
 * set headers on WebSockets); the runtime also checks the page origin. */
export class EventStream {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private topics = new Set<string>();

  constructor(
    private ep: Endpoint,
    private onEnvelope: (env: EventEnvelope) => void,
    private onStatus: (s: StreamStatus, detail?: string) => void,
  ) {}

  start() {
    this.closed = false;
    this.open();
  }

  stop() {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
  }

  subscribe(topic: "resources" | "mic_level", on: boolean) {
    if (on) this.topics.add(topic);
    else this.topics.delete(topic);
    this.send({ op: on ? "subscribe" : "unsubscribe", topics: [topic] });
  }

  private send(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  private open() {
    this.onStatus("connecting");
    const url = this.ep.url.replace(/^http/, "ws") + "/api/v1/events";
    const ws = new WebSocket(url, ["scar.v1", `auth.${this.ep.token}`]);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.onStatus("online");
      if (this.topics.size) this.send({ op: "subscribe", topics: [...this.topics] });
    };
    ws.onmessage = (m) => {
      try {
        this.onEnvelope(JSON.parse(String(m.data)) as EventEnvelope);
      } catch {
        /* a malformed frame is ignored; the next snapshot resynchronises */
      }
    };
    ws.onclose = () => {
      this.ws = null;
      if (this.closed) return;
      this.onStatus("offline");
      const delay = Math.min(10_000, 500 * 2 ** this.attempt++);
      this.timer = setTimeout(() => this.open(), delay);
    };
  }
}
