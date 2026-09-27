// The only place internals appear: doctor checks, logs, providers and models, and a redacted diagnostic bundle.
import clsx from "clsx";
import { CheckCircle2, CircleAlert, CircleX, Download, RefreshCw } from "lucide-react";
import { useState } from "react";
import { humanError } from "../api/client";
import { Badge, Button, Card, EmptyState, ErrorState, IconButton, Skeleton } from "../components/ui";
import { useApiGet } from "../hooks/useApi";
import { revealPath } from "../lib/shell";
import { useRuntime } from "../store/runtime";
import { useUi } from "../store/ui";

interface Check { ok: boolean | null; label: string; hint: string; section: string }
interface LogLine { timestamp?: string; at?: string; level?: string; logger?: string; event?: string; kind?: string; message?: string; [k: string]: unknown }
interface Provider { id: string; configured: boolean; local: boolean; health: { model: string; state: string; last_error: string }[] }

function fixFor(c: Check): { label: string; route: [string, string] } | null {
  const t = `${c.label} ${c.hint}`.toLowerCase();
  if (/groq|gemini|cloud reasoning|api_key|not configured \(missing/.test(t)) return { label: "Add a key", route: ["settings", "ai"] };
  if (/microphone|audio|speaker/.test(t)) return { label: "Voice settings", route: ["settings", "voice"] };
  if (/oauth|gmail|outlook|telegram|discord/.test(t)) return { label: "Accounts", route: ["settings", "accounts"] };
  return null;
}

export function Diagnostics() {
  const doctor = useApiGet<Check[]>("/doctor");
  const [level, setLevel] = useState("info");
  const [task, setTask] = useState("");
  const logs = useApiGet<LogLine[]>(`/logs?limit=300&level=${level}${task ? `&task=${encodeURIComponent(task)}` : ""}`, [level, task]);
  const providers = useApiGet<{ providers: Provider[]; last_route: Record<string, string> }>("/providers");
  const api = useRuntime((s) => s.api);
  const go = useUi((s) => s.go);
  const [bundle, setBundle] = useState<{ busy: boolean; path?: string; error?: string }>({ busy: false });

  async function exportBundle() {
    setBundle({ busy: true });
    try {
      const r = await api!.post<{ path: string }>("/diagnostics/bundle");
      setBundle({ busy: false, path: r.path });
    } catch (e) {
      setBundle({ busy: false, error: humanError(e) });
    }
  }

  const sections = new Map<string, Check[]>();
  for (const c of doctor.data ?? []) sections.set(c.section, [...(sections.get(c.section) ?? []), c]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Diagnostics</h1>
        <Button icon={<Download size={15} aria-hidden />} busy={bundle.busy} onClick={() => void exportBundle()}>
          Export diagnostic bundle
        </Button>
      </div>
      {bundle.path && (
        <p className="muted small">
          Saved {bundle.path} (secrets redacted; no memories, database or screenshots).{" "}
          <button className="link-btn" onClick={() => void revealPath(bundle.path!)}>
            Show in Explorer
          </button>
        </p>
      )}
      {bundle.error && <ErrorState message={bundle.error} />}
      <Card title="Health check" actions={<IconButton label="Run again" onClick={() => void doctor.reload()}><RefreshCw size={16} /></IconButton>}>
        {doctor.loading ? (
          <Skeleton lines={8} />
        ) : doctor.error ? (
          <ErrorState message={doctor.error} />
        ) : (
          [...sections.entries()].map(([sec, checks]) => (
            <div key={sec} className="check-group">
              <h3>{sec.replace(/_/g, " ")}</h3>
              <ul className="checks">
                {checks.map((c, i) => {
                  const fix = c.ok === true ? null : fixFor(c);
                  return (
                    <li key={i} className={clsx("check", c.ok === true ? "ok" : c.ok === false ? "bad" : "warn")}>
                      {c.ok === true ? <CheckCircle2 size={15} aria-label="OK" /> : c.ok === false ? <CircleX size={15} aria-label="Problem" /> : <CircleAlert size={15} aria-label="Note" />}
                      <span className="check-label">{c.label}</span>
                      {c.hint && <span className="check-hint">{c.hint}</span>}
                      {fix && (
                        <Button size="sm" variant="ghost" onClick={() => go(fix.route[0] as "settings", fix.route[1])}>
                          {fix.label}
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </Card>
      <Card title="Providers and models" subtitle="Which AI services SCAR can use and how they are doing.">
        {providers.loading ? (
          <Skeleton />
        ) : providers.error ? (
          <ErrorState message={providers.error} />
        ) : (
          <>
            <p className="muted small">Last used: {Object.entries(providers.data?.last_route ?? {}).map(([k, v]) => `${k} → ${v}`).join(" · ") || "nothing yet this session"}</p>
            <ul className="rows compact">
              {(providers.data?.providers ?? []).filter((p) => p.configured).map((p) => (
                <li key={p.id} className="row">
                  <span className="row-title mono">{p.id}</span>
                  {p.local && <Badge>local</Badge>}
                  <span className="row-sub">
                    {p.health.length ? p.health.map((h) => `${h.model || "(all)"}: ${h.state}${h.last_error ? ` — ${h.last_error.slice(0, 100)}` : ""}`).join("; ") : "no calls yet"}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <Card
        title="Logs"
        actions={
          <div className="toolbar">
            <select aria-label="Minimum level" value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="debug">Everything</option>
              <option value="info">Info and above</option>
              <option value="warning">Warnings and errors</option>
              <option value="error">Errors only</option>
            </select>
            <input aria-label="Task id" placeholder="Task id (optional)" value={task} onChange={(e) => setTask(e.target.value.trim())} />
            <IconButton label="Refresh logs" onClick={() => void logs.reload()}>
              <RefreshCw size={16} />
            </IconButton>
          </div>
        }
      >
        {logs.loading ? (
          <Skeleton lines={6} />
        ) : logs.error ? (
          <ErrorState message={logs.error} />
        ) : !logs.data?.length ? (
          <EmptyState title="No log lines match." />
        ) : (
          <div className="logs" role="log">
            {logs.data.map((l, i) => {
              const { timestamp, at, level: lv, logger, event, kind, message, ...rest } = l;
              return (
                <div key={i} className={clsx("log-line", `log-${lv ?? "info"}`)}>
                  <span className="log-time">{String(timestamp ?? at ?? "").slice(11, 19)}</span>
                  <span className="log-level">{lv ?? kind}</span>
                  <span className="log-msg">
                    {logger ? `${logger}: ` : ""}
                    {event ?? message ?? ""} {Object.keys(rest).length ? JSON.stringify(rest).slice(0, 240) : ""}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
