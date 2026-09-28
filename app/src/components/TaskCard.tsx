// One request in the conversation: what you asked, what SCAR is doing (plain-language timeline), the streamed
// reply, and an honest outcome badge. Details (step results, failed checks) are one click away.
import clsx from "clsx";
import { Ban, Check, ChevronRight, CircleAlert, CircleDashed, CircleX, Loader2, Square } from "lucide-react";
import { useState } from "react";
import type { TaskDetail } from "../api/types";
import { SafeMarkdown } from "../lib/SafeMarkdown";
import type { LiveTask, Outcome, StepLine } from "../store/reducer";
import { useRuntime } from "../store/runtime";
import { Button } from "./ui";

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  switch (outcome.kind) {
    case "running":
      return (
        <span className="outcome outcome-running">
          <Loader2 size={14} className="spin" aria-hidden /> Working
        </span>
      );
    case "verified":
      return (
        <span className="outcome outcome-ok" title="SCAR checked that this really happened">
          <Check size={14} aria-hidden /> Done · verified
        </span>
      );
    case "info":
      return (
        <span className="outcome outcome-muted">
          <Check size={14} aria-hidden /> Answered
        </span>
      );
    case "unverified":
      return (
        <span className="outcome outcome-warn" title={outcome.reason}>
          <CircleAlert size={14} aria-hidden /> Done · couldn't verify
        </span>
      );
    case "failed":
      return (
        <span className="outcome outcome-danger" title={outcome.reason}>
          <CircleX size={14} aria-hidden /> Failed
        </span>
      );
    case "cancelled":
      return (
        <span className="outcome outcome-muted">
          <Ban size={14} aria-hidden /> Cancelled
        </span>
      );
  }
}

function StepIcon({ status }: { status: StepLine["status"] }) {
  if (status === "running") return <Loader2 size={14} className="spin" aria-label="in progress" />;
  if (status === "ok") return <Check size={14} aria-label="done" />;
  if (status === "denied") return <Ban size={14} aria-label="not allowed" />;
  if (status === "cancelled") return <CircleDashed size={14} aria-label="stopped" />;
  return <CircleX size={14} aria-label="failed" />;
}

export function TaskCard({ task }: { task: LiveTask }) {
  const cancel = useRuntime((s) => s.cancel);
  const api = useRuntime((s) => s.api);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const running = task.outcome.kind === "running";
  const waiting = useRuntime((s) => Object.values(s.approvals).some((a) => a.task_id === task.taskId));

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !running && api && !detail) {
      try {
        setDetail(await api.get<TaskDetail>(`/tasks/${encodeURIComponent(task.taskId)}`));
      } catch {
        /* details are optional; the timeline is already shown */
      }
    }
  }

  const reply = running ? task.draft : task.result;
  const visibleSteps = task.steps.filter((s) => s.tool !== "finish" && s.tool !== "read_artifact");

  return (
    <div className="task" data-task={task.taskId}>
      <div className="msg-user" aria-label="You asked">
        <p>{task.objective || "…"}</p>
      </div>
      <div className={clsx("msg-scar", `msg-${task.outcome.kind}`)} aria-live={running ? "polite" : undefined}>
        {visibleSteps.length > 0 && (
          <ol className="timeline" aria-label="What SCAR did">
            {visibleSteps.map((s) => (
              <li key={s.actionId} className={clsx("step", `step-${s.status}`)}>
                <StepIcon status={s.status} />
                <span>{s.title}</span>
                {s.status !== "running" && s.status !== "ok" && s.detail && <span className="step-detail">— {s.detail}</span>}
              </li>
            ))}
          </ol>
        )}
        {running && waiting && <p className="progress-line waiting">Waiting for your permission below.</p>}
        {running && !waiting && task.progress && !task.draft && <p className="progress-line">{task.progress}</p>}
        {reply ? <SafeMarkdown text={reply} /> : running && !waiting && !visibleSteps.length && <p className="thinking">Thinking…</p>}
        <div className="task-foot">
          <OutcomeBadge outcome={task.outcome} />
          {task.outcome.kind === "failed" && <span className="task-reason">{task.outcome.reason}</span>}
          {task.outcome.kind === "unverified" && <span className="task-reason">{task.outcome.reason}</span>}
          <span className="spacer" />
          {running && (
            <Button size="sm" variant="ghost" icon={<Square size={12} aria-hidden />} onClick={() => void cancel(task.taskId)}>
              Cancel
            </Button>
          )}
          {(visibleSteps.length > 0 || !running) && (
            <button className="link-btn" aria-expanded={open} onClick={() => void toggle()}>
              <ChevronRight size={14} className={clsx("chev", open && "open")} aria-hidden /> Details
            </button>
          )}
        </div>
        {open && (
          <div className="task-details">
            {(detail?.steps ?? []).length === 0 && visibleSteps.length === 0 && <p className="muted">No actions were needed.</p>}
            {(detail?.steps ?? visibleSteps.map((s) => ({ tool: s.tool, title: s.title, status: s.status, summary: s.detail, risk: s.risk, decision: "", verified: null, duration_ms: null }))).map((s, i) => (
              <div key={i} className="detail-row">
                <span className="detail-title">{s.title}</span>
                <span className="detail-meta">
                  {s.risk && `${s.risk.toLowerCase()} risk · `}
                  {s.status}
                  {s.verified === true && " · checked"}
                  {s.duration_ms != null && ` · ${s.duration_ms} ms`}
                </span>
                {s.summary && <span className="detail-summary">{s.summary}</span>}
              </div>
            ))}
            {detail && (detail.failed_checks ?? []).length > 0 && <p className="detail-checks">Checks that failed: {(detail.failed_checks ?? []).join(", ")}</p>}
            {detail?.verification_note && <p className="detail-checks">{detail.verification_note}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
