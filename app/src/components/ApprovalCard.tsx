// An approval request, shown exactly: what will happen, why SCAR is asking, and how risky it is.
// Decisions are sent only from trusted user gestures (a real click or key press on this card) and are bound to the
// request id and args hash the card displays. CRITICAL actions need the confirmation word typed.
import * as Menu from "@radix-ui/react-dropdown-menu";
import clsx from "clsx";
import { ChevronDown, ShieldAlert, ShieldCheck, Timer } from "lucide-react";
import { useEffect, useId, useMemo, useState, type MouseEvent, type KeyboardEvent } from "react";
import type { ApprovalView } from "../api/types";
import { useRuntime } from "../store/runtime";
import { Badge, Button } from "./ui";

const RISK_TONE = { LOW: "neutral", MEDIUM: "info", HIGH: "warn", CRITICAL: "danger" } as const;
const RISK_WORD = { LOW: "Low risk", MEDIUM: "Medium risk", HIGH: "High risk", CRITICAL: "Critical" } as const;

const MORE: Record<string, { label: string; scope: string }> = {
  allow_session: { label: "Allow until SCAR restarts", scope: "Matching actions run without asking until you quit SCAR." },
  allow_timed: { label: "Allow for 60 minutes", scope: "Matching actions run without asking for the next hour." },
  allow_always: { label: "Always allow this exact action", scope: "Saved as a permission you can revoke in Permissions." },
  deny_always: { label: "Always deny this", scope: "SCAR will refuse this action without asking again." },
};

const DETAIL_LABELS: Record<string, string> = {
  to: "To", cc: "Cc", recipient: "Recipient", recipients: "Recipients", subject: "Subject", body: "Message",
  attachments: "Attachments", paths: "Files", file_count: "Number of files", command: "Command", cwd: "In folder",
  shell: "Shell", diff: "Changes", code: "Code", url: "Web address",
};

function remaining(expiresAt: string): number {
  const t = Date.parse(expiresAt);
  return Number.isFinite(t) ? Math.max(0, Math.round((t - Date.now()) / 1000)) : 0;
}

function fmt(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function detailValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(String).join("\n");
  if (v && typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v ?? "");
}

export function ApprovalCard({ approval, compact }: { approval: ApprovalView; compact?: boolean }) {
  const answer = useRuntime((s) => s.answerApproval);
  const [left, setLeft] = useState(() => remaining(approval.expires_at));
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const inputId = useId();
  const allowed = new Set(approval.allowed_responses);

  useEffect(() => {
    const t = setInterval(() => setLeft(remaining(approval.expires_at)), 1000);
    return () => clearInterval(t);
  }, [approval.expires_at]);

  const details = useMemo(
    () => Object.entries(approval.details ?? {}).filter(([k, v]) => DETAIL_LABELS[k] && v !== null && v !== "" && !(Array.isArray(v) && !v.length)),
    [approval.details],
  );
  const expired = left <= 0;
  const codeOk = !approval.critical || typed.trim().toUpperCase() === approval.confirmation_code.toUpperCase();

  async function decide(response: string, e: MouseEvent | KeyboardEvent) {
    const trusted = e.nativeEvent.isTrusted;
    if (!trusted && response !== "deny") return; // synthetic events never approve
    setBusy(response);
    setError("");
    const err = await answer(approval, response, trusted, approval.critical ? typed : undefined);
    setBusy(null);
    if (err) setError(err);
  }

  return (
    <article className={clsx("approval", `approval-${approval.risk.toLowerCase()}`, compact && "approval-compact")} aria-label={`Approval needed: ${approval.summary}`}>
      <header className="approval-head">
        {approval.risk === "CRITICAL" || approval.risk === "HIGH" ? <ShieldAlert size={18} aria-hidden /> : <ShieldCheck size={18} aria-hidden />}
        <h3>SCAR needs your permission</h3>
        <Badge tone={RISK_TONE[approval.risk]}>{RISK_WORD[approval.risk]}</Badge>
        <span className={clsx("approval-timer", left < 30 && "urgent")} aria-live="off" title="When the time runs out, the request is denied">
          <Timer size={13} aria-hidden /> {expired ? "Expired — denied" : fmt(left)}
        </span>
      </header>
      <p className="approval-summary">{approval.summary}</p>
      {details.length > 0 && (
        <dl className="approval-details">
          {details.map(([k, v]) => (
            <div key={k} className="approval-detail">
              <dt>{DETAIL_LABELS[k]}</dt>
              <dd>
                <pre>{detailValue(v)}</pre>
              </dd>
            </div>
          ))}
        </dl>
      )}
      <p className="approval-reason">
        <strong>Why SCAR is asking:</strong> {approval.reason}
      </p>
      {approval.critical && (
        <div className="approval-confirm">
          <label htmlFor={inputId}>
            Type <code>{approval.confirmation_code}</code> to allow this once
          </label>
          <input id={inputId} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} disabled={expired} />
        </div>
      )}
      {error && (
        <p className="approval-error" role="alert">
          {error}
        </p>
      )}
      <footer className="approval-actions">
        <Button variant="primary" busy={busy === "allow_once"} disabled={expired || !codeOk || !!busy} onClick={(e) => void decide("allow_once", e)}>
          Allow once
        </Button>
        {allowed.has("allow_task") && (
          <Button busy={busy === "allow_task"} disabled={expired || !!busy} onClick={(e) => void decide("allow_task", e)}>
            Allow for this task
          </Button>
        )}
        <Button variant="ghost" busy={busy === "deny"} disabled={expired || !!busy} onClick={(e) => void decide("deny", e)}>
          Deny
        </Button>
        {Object.keys(MORE).some((r) => allowed.has(r)) && (
          <Menu.Root>
            <Menu.Trigger asChild>
              <button className="btn btn-ghost btn-md approval-more" disabled={expired || !!busy}>
                <span>Always…</span>
                <ChevronDown size={14} aria-hidden />
              </button>
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Content className="menu" align="end" sideOffset={6}>
                {Object.entries(MORE)
                  .filter(([r]) => allowed.has(r))
                  .map(([r, m]) => (
                    <Menu.Item key={r} className="menu-item" onClick={(e) => void decide(r, e as unknown as MouseEvent)}>
                      <span className="menu-item-label">{m.label}</span>
                      <span className="menu-item-sub">{m.scope}</span>
                    </Menu.Item>
                  ))}
              </Menu.Content>
            </Menu.Portal>
          </Menu.Root>
        )}
      </footer>
    </article>
  );
}
