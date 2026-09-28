// Pure state transitions from runtime events. Kept free of I/O so every UI state can be unit-tested, including the
// honesty rules: the UI only shows "Done" when the runtime reported success, and "verified" only when it verified.
import type { ApprovalView, EventEnvelope, Snapshot } from "../api/types";

export type Outcome =
  | { kind: "running" }
  | { kind: "verified" }
  | { kind: "info" }
  | { kind: "unverified"; reason: string }
  | { kind: "failed"; reason: string }
  | { kind: "cancelled" };

export interface StepLine {
  actionId: string;
  tool: string;
  title: string;
  status: "running" | "ok" | "error" | "denied" | "timeout" | "cancelled" | "unavailable";
  detail: string;
  risk: string;
}

export interface LiveTask {
  taskId: string;
  objective: string;
  origin: string;
  background: boolean;
  steps: StepLine[];
  progress: string;
  draft: string;
  result: string;
  outcome: Outcome;
  startedAt: string;
}

export interface Notice {
  id: string;
  text: string;
  at: string;
  tone: "info" | "warn";
}

export interface QuestionView {
  questionId: string;
  taskId: string | null;
  text: string;
  options: string[];
}

export interface VoiceView {
  state: string;
  mic_active: boolean;
  muted: boolean;
  mode: string;
  degraded?: string;
  transcript?: string;
}

export interface ProviderView {
  level: "cloud" | "local" | "degraded" | "none";
  summary: string;
  retry_in_s?: number | null;
}

export interface RuntimeState {
  assistant: Snapshot["state"];
  provider: ProviderView;
  voice: VoiceView;
  gameMode: boolean;
  killKey: string;
  approvals: Record<string, ApprovalView>;
  questions: Record<string, QuestionView>;
  tasks: Record<string, LiveTask>;
  order: string[]; // task ids in conversation order
  notices: Notice[];
  control: { active: boolean; what: string };
  captureAt: number;
  micLevel: number;
  resources: Record<string, unknown> | null;
  lastSeq: number;
  openAppAt: number;
}

export const initialState: RuntimeState = {
  assistant: "offline",
  provider: { level: "none", summary: "Connecting to SCAR…" },
  voice: { state: "off", mic_active: false, muted: false, mode: "" },
  gameMode: false,
  killKey: "",
  approvals: {},
  questions: {},
  tasks: {},
  order: [],
  notices: [],
  control: { active: false, what: "" },
  captureAt: 0,
  micLevel: 0,
  resources: null,
  lastSeq: 0,
  openAppAt: 0,
};

const COULDNT_VERIFY = /\(I couldn't verify this automatically\.\)|Not independently confirmed[^)]*\)?/;
const VERIFY_FAILED = /However, verification failed: (.+)$/;

/** The badge for a finished task, derived only from what the runtime reported. */
export function outcomeFor(status: string, verified: boolean | null | undefined, message: string): Outcome {
  if (status === "cancelled" || /^Cancelled/.test(message)) return { kind: "cancelled" };
  if (status !== "succeeded") {
    const vf = VERIFY_FAILED.exec(message);
    return { kind: "failed", reason: vf?.[1] ?? firstSentence(message) };
  }
  if (verified === true) return { kind: "verified" };
  // an action SCAR could not confirm is "couldn't verify"; a plain answer has nothing to confirm
  return COULDNT_VERIFY.test(message) ? { kind: "unverified", reason: "SCAR had no way to check the result automatically" } : { kind: "info" };
}

function firstSentence(text: string): string {
  const s = text.split(/(?<=[.!?])\s/)[0] ?? text;
  return s.length > 180 ? s.slice(0, 177) + "…" : s;
}

function ensureTask(st: RuntimeState, taskId: string, objective = "", origin = "text", background = false): LiveTask {
  const existing = st.tasks[taskId];
  if (existing) return existing;
  const t: LiveTask = {
    taskId,
    objective,
    origin,
    background,
    steps: [],
    progress: "",
    draft: "",
    result: "",
    outcome: { kind: "running" },
    startedAt: new Date().toISOString(),
  };
  st.tasks = { ...st.tasks, [taskId]: t };
  if (!st.order.includes(taskId)) st.order = [...st.order, taskId];
  return t;
}

function updateTask(st: RuntimeState, taskId: string, patch: (t: LiveTask) => LiveTask) {
  const t = st.tasks[taskId];
  if (!t) return;
  st.tasks = { ...st.tasks, [taskId]: patch(t) };
}

function applySnapshot(st: RuntimeState, snap: Snapshot): void {
  st.assistant = snap.state;
  st.provider = snap.provider as ProviderView;
  st.voice = snap.voice as unknown as VoiceView;
  st.gameMode = snap.game_mode;
  st.killKey = snap.killswitch_hotkey;
  st.approvals = Object.fromEntries(snap.pending_approvals.map((a) => [a.request_id, a]));
  for (const t of snap.running_tasks) {
    ensureTask(st, t.task_id, t.objective, t.origin ?? "text", t.background ?? false);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Ev = Record<string, any>;

export function reduce(prev: RuntimeState, env: EventEnvelope): RuntimeState {
  const st: RuntimeState = { ...prev, lastSeq: env.seq };
  if (env.type === "snapshot" && env.snapshot) {
    applySnapshot(st, env.snapshot);
    return st;
  }
  if (env.type !== "event" || !env.event) return st;
  const e = env.event as Ev;
  const tid: string | null = e.task_id ?? null;
  switch (e.kind) {
    case "task_started":
      if (tid) ensureTask(st, tid, e.objective ?? "", "text", Boolean(e.background));
      break;
    case "tool_called":
      if (tid) {
        ensureTask(st, tid);
        updateTask(st, tid, (t) => ({
          ...t,
          draft: "",
          steps: [...t.steps, { actionId: e.action_id, tool: e.tool, title: e.title ?? e.tool, status: "running", detail: "", risk: e.risk ?? "" }],
        }));
      }
      break;
    case "tool_completed":
      if (tid)
        updateTask(st, tid, (t) => ({
          ...t,
          steps: t.steps.map((s) =>
            s.actionId === e.action_id ? { ...s, status: (e.status as StepLine["status"]) ?? "ok", detail: e.message ?? "" } : s,
          ),
        }));
      break;
    case "task_progress":
      if (tid) updateTask(st, tid, (t) => ({ ...t, progress: e.message ?? "" }));
      else if (e.message) st.notices = [...st.notices.slice(-49), { id: `n${env.seq}`, text: e.message, at: e.at, tone: "info" }];
      break;
    case "assistant_delta":
      if (tid) updateTask(st, tid, (t) => ({ ...t, draft: e.reset ? "" : t.draft + (e.text ?? "") }));
      break;
    case "task_completed":
      if (tid) {
        ensureTask(st, tid);
        updateTask(st, tid, (t) => ({
          ...t,
          draft: "",
          result: e.message ?? "",
          outcome: outcomeFor("succeeded", e.verified, e.message ?? ""),
          steps: t.steps.map((s) => (s.status === "running" ? { ...s, status: "ok" } : s)),
        }));
      }
      break;
    case "task_failed":
      if (tid) {
        ensureTask(st, tid);
        updateTask(st, tid, (t) => ({
          ...t,
          draft: "",
          result: e.message ?? "",
          outcome: outcomeFor(e.error || "failed", false, e.message ?? ""),
          steps: t.steps.map((s) => (s.status === "running" ? { ...s, status: "cancelled" } : s)),
        }));
      }
      break;
    case "approval_requested":
      if (e.approval) st.approvals = { ...st.approvals, [e.approval.request_id]: e.approval as ApprovalView };
      break;
    case "approval_resolved": {
      const rest = { ...st.approvals };
      delete rest[e.request_id];
      st.approvals = rest;
      break;
    }
    case "question_asked":
      st.questions = {
        ...st.questions,
        [e.question_id]: { questionId: e.question_id, taskId: tid, text: e.message ?? "", options: e.options ?? [] },
      };
      break;
    case "question_answered": {
      const rest = { ...st.questions };
      delete rest[e.question_id];
      st.questions = rest;
      break;
    }
    case "assistant_state":
      st.assistant = e.state;
      break;
    case "provider_health":
    case "provider_fallback":
      if (e.provider) st.provider = e.provider as ProviderView;
      break;
    case "voice_state":
      st.voice = { state: e.state, mic_active: e.mic_active, muted: e.muted, mode: e.mode, transcript: e.transcript };
      break;
    case "mic_level":
      st.micLevel = e.level ?? 0;
      break;
    case "control_active":
      st.control = { active: Boolean(e.active), what: e.what ?? "" };
      break;
    case "screen_captured":
      st.captureAt = Date.parse(e.at) || Date.now();
      break;
    case "resource_snapshot":
      st.resources = e.snapshot ?? null;
      break;
    case "open_app":
      st.openAppAt = Date.now();
      break;
    case "game_mode":
      st.gameMode = Boolean(e.active);
      break;
    case "assistant_message":
      if (!tid && e.message) st.notices = [...st.notices.slice(-49), { id: `n${env.seq}`, text: e.message, at: e.at, tone: "info" }];
      break;
    case "resource_warning":
      st.notices = [...st.notices.slice(-49), { id: `n${env.seq}`, text: e.message ?? "Resources are low", at: e.at, tone: "warn" }];
      break;
    default:
      break;
  }
  return st;
}

/** Tasks that end the "working" state for Stop All and the tray. */
export function runningTaskIds(st: RuntimeState): string[] {
  return st.order.filter((id) => st.tasks[id]?.outcome.kind === "running");
}
