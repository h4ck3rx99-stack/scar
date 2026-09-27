/* Generated from the runtime's Pydantic models by scripts/gen_api_types.py + json2ts. Do not edit. */

export interface ScarApi {
  ApprovalAnswer?: ApprovalAnswer;
  ApprovalRequested?: ApprovalRequested;
  ApprovalResolved?: ApprovalResolved;
  ApprovalView?: ApprovalView;
  AssistantDelta?: AssistantDelta;
  AssistantMessage?: AssistantMessage;
  AssistantState?: AssistantState;
  ControlActive?: ControlActive;
  EventEnvelope?: EventEnvelope;
  GameModeChanged?: GameModeChanged;
  Hello?: Hello;
  MemoryEdit?: MemoryEdit;
  MicLevel?: MicLevel;
  MonitorFired?: MonitorFired;
  NotificationShown?: NotificationShown;
  OpenAppRequested?: OpenAppRequested;
  PlanCreated?: PlanCreated;
  ProviderFallback?: ProviderFallback;
  ProviderHealthChanged?: ProviderHealthChanged;
  ProviderSummary?: ProviderSummary;
  QuestionAnswer?: QuestionAnswer;
  ReminderDue?: ReminderDue;
  ResourceSnapshotEvent?: ResourceSnapshotEvent;
  ResourceWarning?: ResourceWarning;
  ScreenCaptured?: ScreenCaptured;
  SecretValue?: SecretValue;
  SettingsPatch?: SettingsPatch;
  Snapshot?: Snapshot;
  StepStarted?: StepStarted;
  StepView?: StepView;
  SubmitTask?: SubmitTask;
  Subscribe?: Subscribe;
  TaskCompleted?: TaskCompleted;
  TaskDetail?: TaskDetail;
  TaskFailed?: TaskFailed;
  TaskProgress?: TaskProgress;
  TaskStarted?: TaskStarted;
  TaskView?: TaskView;
  ToolCalled?: ToolCalled;
  ToolCompleted?: ToolCompleted;
  VerificationResultEvent?: VerificationResultEvent;
  VoiceCommand?: VoiceCommand;
  VoiceStateEvent?: VoiceStateEvent;
  WipeMemory?: WipeMemory;
}
/**
 * A user's decision on one pending approval. ``args_hash`` must match the pending request (the UI echoes what it
 * displayed), and ``user_gesture`` records that it came from a trusted click/keypress on the approval card.
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ApprovalAnswer".
 */
export interface ApprovalAnswer {
  args_hash: string;
  response: "allow_once" | "allow_task" | "allow_session" | "allow_timed" | "allow_always" | "deny" | "deny_always";
  typed_confirmation?: string | null;
  user_gesture?: boolean;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ApprovalRequested".
 */
export interface ApprovalRequested {
  at?: string;
  kind?: "approval_requested";
  message?: string;
  request_id?: string;
  risk?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ApprovalResolved".
 */
export interface ApprovalResolved {
  at?: string;
  decision?: string;
  kind?: "approval_resolved";
  message?: string;
  request_id?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ApprovalView".
 */
export interface ApprovalView {
  allowed_responses: string[];
  args_hash: string;
  confirmation_code: string;
  created_at: string;
  critical: boolean;
  details: {
    [k: string]: unknown;
  };
  expires_at: string;
  grantable: boolean;
  reason: string;
  request_id: string;
  risk: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  summary: string;
  task_id: string | null;
  tool: string;
}
/**
 * Streamed reply text as it is generated. ``reset`` = discard the partial text (the model is retrying).
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "AssistantDelta".
 */
export interface AssistantDelta {
  at?: string;
  kind?: "assistant_delta";
  message?: string;
  reset?: boolean;
  task_id?: string | null;
  text?: string;
  [k: string]: unknown;
}
/**
 * A user-facing reply chunk (conversation text, final answers).
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "AssistantMessage".
 */
export interface AssistantMessage {
  at?: string;
  final?: boolean;
  kind?: "assistant_message";
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * What SCAR is doing, in one word, for status indicators.
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "AssistantState".
 */
export interface AssistantState {
  at?: string;
  kind?: "assistant_state";
  message?: string;
  state?: "ready" | "working" | "listening" | "waiting" | "speaking" | "offline";
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * SCAR is (or stopped) moving the mouse, typing or controlling windows; the UI shows an indicator.
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ControlActive".
 */
export interface ControlActive {
  active?: boolean;
  at?: string;
  kind?: "control_active";
  message?: string;
  task_id?: string | null;
  what?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "EventEnvelope".
 */
export interface EventEnvelope {
  event?: {
    [k: string]: unknown;
  } | null;
  seq: number;
  snapshot?: Snapshot | null;
  type: "snapshot" | "event" | "error";
  v?: 1;
}
/**
 * Everything a freshly connected client needs to render the current state (sent first on the event stream).
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "Snapshot".
 */
export interface Snapshot {
  game_mode: boolean;
  killswitch_hotkey: string;
  pending_approvals: ApprovalView[];
  provider: ProviderSummary;
  running_tasks: TaskView[];
  state: "ready" | "working" | "listening" | "waiting" | "speaking" | "offline";
  voice: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ProviderSummary".
 */
export interface ProviderSummary {
  level: "cloud" | "local" | "degraded" | "none";
  retry_in_s?: number | null;
  summary: string;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskView".
 */
export interface TaskView {
  background?: boolean;
  created_at?: string | null;
  failed_checks?: string[];
  finished_at?: string | null;
  objective: string;
  origin?: string;
  result_summary: string;
  status: string;
  task_id: string;
  verification_note?: string;
  verified: boolean | null;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "GameModeChanged".
 */
export interface GameModeChanged {
  active?: boolean;
  at?: string;
  kind?: "game_mode";
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "Hello".
 */
export interface Hello {
  api_version: string;
  app_version: string;
  data_dir: string;
  pid: number;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "MemoryEdit".
 */
export interface MemoryEdit {
  text: string;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "MicLevel".
 */
export interface MicLevel {
  at?: string;
  kind?: "mic_level";
  level?: number;
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "MonitorFired".
 */
export interface MonitorFired {
  at?: string;
  detail?: {
    [k: string]: unknown;
  };
  kind?: "monitor_fired";
  message?: string;
  monitor_id?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "NotificationShown".
 */
export interface NotificationShown {
  at?: string;
  kind?: "notification";
  message?: string;
  task_id?: string | null;
  title?: string;
  urgent?: boolean;
  [k: string]: unknown;
}
/**
 * The user clicked a SCAR toast: the desktop app should come to the front (toasts never approve anything).
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "OpenAppRequested".
 */
export interface OpenAppRequested {
  at?: string;
  kind?: "open_app";
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "PlanCreated".
 */
export interface PlanCreated {
  at?: string;
  kind?: "plan_created";
  message?: string;
  steps?: string[];
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ProviderFallback".
 */
export interface ProviderFallback {
  at?: string;
  category?: string;
  from_provider?: string;
  kind?: "provider_fallback";
  message?: string;
  reason?: string;
  task_id?: string | null;
  to_provider?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ProviderHealthChanged".
 */
export interface ProviderHealthChanged {
  at?: string;
  kind?: "provider_health";
  level?: "cloud" | "local" | "degraded" | "none";
  message?: string;
  summary?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "QuestionAnswer".
 */
export interface QuestionAnswer {
  text: string;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ReminderDue".
 */
export interface ReminderDue {
  at?: string;
  kind?: "reminder_due";
  message?: string;
  schedule_id?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ResourceSnapshotEvent".
 */
export interface ResourceSnapshotEvent {
  at?: string;
  kind?: "resource_snapshot";
  message?: string;
  snapshot?: {
    [k: string]: unknown;
  };
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ResourceWarning".
 */
export interface ResourceWarning {
  at?: string;
  kind?: "resource_warning";
  message?: string;
  resource?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ScreenCaptured".
 */
export interface ScreenCaptured {
  at?: string;
  kind?: "screen_captured";
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "SecretValue".
 */
export interface SecretValue {
  value: string;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "SettingsPatch".
 */
export interface SettingsPatch {
  values: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "StepStarted".
 */
export interface StepStarted {
  at?: string;
  intent?: string;
  kind?: "step_started";
  message?: string;
  step?: number;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "StepView".
 */
export interface StepView {
  decision?: string;
  duration_ms?: number | null;
  risk?: string;
  status: string;
  summary: string;
  title: string;
  tool: string;
  verified?: boolean | null;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "SubmitTask".
 */
export interface SubmitTask {
  autonomy?: number | null;
  background?: boolean;
  dry_run?: boolean | null;
  objective: string;
  origin?: "text" | "voice";
}
/**
 * Sent on the event stream to opt into high-rate topics (only while a screen that shows them is open).
 *
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "Subscribe".
 */
export interface Subscribe {
  op: "subscribe" | "unsubscribe";
  topics: ("resources" | "mic_level")[];
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskCompleted".
 */
export interface TaskCompleted {
  at?: string;
  background?: boolean;
  kind?: "task_completed";
  message?: string;
  task_id?: string | null;
  verified?: boolean | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskDetail".
 */
export interface TaskDetail {
  background?: boolean;
  created_at?: string | null;
  failed_checks?: string[];
  finished_at?: string | null;
  objective: string;
  origin?: string;
  result_summary: string;
  status: string;
  steps?: StepView[];
  task_id: string;
  verification_note?: string;
  verified: boolean | null;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskFailed".
 */
export interface TaskFailed {
  at?: string;
  background?: boolean;
  error?: string;
  kind?: "task_failed";
  message?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskProgress".
 */
export interface TaskProgress {
  at?: string;
  kind?: "task_progress";
  message?: string;
  milestone?: boolean;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "TaskStarted".
 */
export interface TaskStarted {
  at?: string;
  background?: boolean;
  kind?: "task_started";
  message?: string;
  objective?: string;
  task_id?: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ToolCalled".
 */
export interface ToolCalled {
  action_id?: string;
  at?: string;
  kind?: "tool_called";
  message?: string;
  risk?: string;
  task_id?: string | null;
  tool?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "ToolCompleted".
 */
export interface ToolCompleted {
  action_id?: string;
  at?: string;
  duration_ms?: number;
  kind?: "tool_completed";
  message?: string;
  status?: string;
  task_id?: string | null;
  tool?: string;
  verified?: boolean | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "VerificationResultEvent".
 */
export interface VerificationResultEvent {
  at?: string;
  kind?: "verification_result";
  message?: string;
  task_id?: string | null;
  verified?: boolean | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "VoiceCommand".
 */
export interface VoiceCommand {
  action: "start" | "stop" | "push_to_talk" | "mute" | "unmute" | "stop_speaking";
  mode?: ("ptt" | "wake") | null;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "VoiceStateEvent".
 */
export interface VoiceStateEvent {
  at?: string;
  kind?: "voice_state";
  message?: string;
  mic_active?: boolean;
  mode?: string;
  muted?: boolean;
  state?: "off" | "idle" | "listening" | "thinking" | "speaking";
  task_id?: string | null;
  transcript?: string;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ScarApi`'s JSON-Schema
 * via the `definition` "WipeMemory".
 */
export interface WipeMemory {
  confirm: "FORGET EVERYTHING";
}
