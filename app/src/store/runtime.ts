// App-wide runtime state (zustand). One connection per window; screens read slices and call actions.
import { create } from "zustand";
import { ApiClient, EventStream, humanError, type StreamStatus } from "../api/client";
import type { ApprovalView, EventEnvelope } from "../api/types";
import { runtimeInfo, type RuntimeInfo } from "../lib/shell";
import { initialState, reduce, type RuntimeState } from "./reducer";

export type Connection = "starting" | "connecting" | "online" | "offline" | "missing" | "crashed";

interface Store extends RuntimeState {
  conn: Connection;
  connMessage: string;
  api: ApiClient | null;
  stream: EventStream | null;
  lastError: string;
  connect: () => Promise<void>;
  disconnect: () => void;
  submit: (objective: string, origin?: "text" | "voice") => Promise<string | null>;
  cancel: (taskId: string) => Promise<void>;
  stopAll: () => Promise<void>;
  answerApproval: (a: ApprovalView, response: string, gesture: boolean, typed?: string) => Promise<string | null>;
  answerQuestion: (questionId: string, text: string) => Promise<void>;
  voice: RuntimeState["voice"];
  voiceCommand: (action: "start" | "stop" | "push_to_talk" | "mute" | "unmute" | "stop_speaking", mode?: "ptt" | "wake") => Promise<string | null>;
  subscribe: (topic: "resources" | "mic_level", on: boolean) => void;
  clearError: () => void;
  dismissNotice: (id: string) => void;
}

let retryTimer: ReturnType<typeof setTimeout> | null = null;

export const useRuntime = create<Store>((set, get) => ({
  ...initialState,
  conn: "starting",
  connMessage: "Starting SCAR…",
  api: null,
  stream: null,
  lastError: "",

  async connect() {
    if (retryTimer) clearTimeout(retryTimer);
    let info: RuntimeInfo;
    try {
      info = await runtimeInfo();
    } catch (e) {
      set({ conn: "crashed", connMessage: humanError(e) });
      return;
    }
    if (!info.endpoint) {
      set({ conn: info.state === "starting" ? "starting" : info.state === "crashed" ? "crashed" : "missing", connMessage: info.message });
      if (info.state === "starting") retryTimer = setTimeout(() => void get().connect(), 700);
      return;
    }
    get().stream?.stop();
    const api = new ApiClient(info.endpoint);
    const stream = new EventStream(
      info.endpoint,
      (env: EventEnvelope) => set((s) => reduce(s, env)),
      (status: StreamStatus) => {
        if (status === "online") set({ conn: "online", connMessage: "" });
        else if (status === "offline") {
          set({ conn: "offline", connMessage: "Lost the connection to SCAR's runtime. Reconnecting…", assistant: "offline" });
          // the runtime may have been restarted with a new port/token: ask the shell again
          retryTimer = setTimeout(() => void get().connect(), 1500);
        } else if (get().conn !== "online") set({ conn: "connecting" });
      },
    );
    set({ api, stream, conn: "connecting" });
    stream.start();
  },

  disconnect() {
    get().stream?.stop();
    set({ stream: null, api: null, conn: "offline" });
  },

  async submit(objective, origin = "text") {
    const api = get().api;
    if (!api) {
      set({ lastError: "SCAR isn't connected yet." });
      return null;
    }
    try {
      const r = await api.post<{ task_id: string }>("/tasks", { objective, origin });
      set((s) => {
        const tasks = { ...s.tasks };
        if (!tasks[r.task_id]) {
          tasks[r.task_id] = {
            taskId: r.task_id, objective, origin, background: false, steps: [], progress: "", draft: "", result: "",
            outcome: { kind: "running" }, startedAt: new Date().toISOString(),
          };
        } else tasks[r.task_id] = { ...tasks[r.task_id]!, objective: tasks[r.task_id]!.objective || objective };
        return { tasks, order: s.order.includes(r.task_id) ? s.order : [...s.order, r.task_id] };
      });
      return r.task_id;
    } catch (e) {
      set({ lastError: humanError(e) });
      return null;
    }
  },

  async cancel(taskId) {
    try {
      await get().api?.post(`/tasks/${encodeURIComponent(taskId)}/cancel`);
    } catch (e) {
      set({ lastError: humanError(e) });
    }
  },

  async stopAll() {
    try {
      await get().api?.post("/stop-all");
    } catch (e) {
      set({ lastError: humanError(e) });
    }
  },

  async answerApproval(a, response, gesture, typed) {
    try {
      await get().api?.post(`/approvals/${encodeURIComponent(a.request_id)}`, {
        response,
        args_hash: a.args_hash,
        user_gesture: gesture,
        typed_confirmation: typed ?? null,
      });
      return null;
    } catch (e) {
      return humanError(e);
    }
  },

  async answerQuestion(questionId, text) {
    try {
      await get().api?.post(`/questions/${encodeURIComponent(questionId)}`, { text });
    } catch (e) {
      set({ lastError: humanError(e) });
    }
  },

  async voiceCommand(action, mode) {
    const api = get().api;
    if (!api) return "SCAR isn't connected yet.";
    try {
      const r = await api.post<{ ok: boolean; message?: string; voice: RuntimeState["voice"] }>("/voice", { action, mode: mode ?? null });
      set({ voice: r.voice });
      return r.ok ? null : r.message ?? "Voice isn't available.";
    } catch (e) {
      return humanError(e);
    }
  },

  subscribe(topic, on) {
    get().stream?.subscribe(topic, on);
  },

  clearError() {
    set({ lastError: "" });
  },

  dismissNotice(id) {
    set((s) => ({ notices: s.notices.filter((n) => n.id !== id) }));
  },
}));
