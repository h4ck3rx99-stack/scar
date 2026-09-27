import { CalendarClock, Eye, ListChecks, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { TaskView } from "../api/types";
import { humanError } from "../api/client";
import { OutcomeBadge, TaskCard } from "../components/TaskCard";
import { Button, Card, EmptyState, ErrorState, IconButton, Skeleton } from "../components/ui";
import { useApiGet } from "../hooks/useApi";
import { outcomeFor } from "../store/reducer";
import { useRuntime } from "../store/runtime";

interface Schedule { schedule_id: string; kind: string; text: string; objective: string | null; next_run: string; interval_seconds: number | null; status: string; fired_count: number }
interface Monitor { monitor_id: string; kind: string; status: string; target_json: string; created_at: string; last_event: string | null }

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function describeMonitor(m: Monitor): string {
  try {
    const t = JSON.parse(m.target_json) as Record<string, unknown>;
    if (m.kind === "process") return `Process ${t.name ?? ""} (PID ${t.pid ?? "?"})`;
    if (m.kind === "folder") return `Folder ${t.path ?? ""}`;
    if (m.kind === "url" || m.kind === "http") return `Web address ${t.url ?? ""}`;
    return `${m.kind}: ${Object.values(t).slice(0, 2).join(" ")}`;
  } catch {
    return m.kind;
  }
}

export function Tasks() {
  const order = useRuntime((s) => s.order);
  const tasks = useRuntime((s) => s.tasks);
  const api = useRuntime((s) => s.api);
  const recent = useApiGet<TaskView[]>("/tasks?limit=40");
  const schedules = useApiGet<Schedule[]>("/schedules");
  const monitors = useApiGet<Monitor[]>("/monitors");
  const [err, setErr] = useState("");
  const running = order.map((id) => tasks[id]).filter((t) => t && t.outcome.kind === "running");

  async function cancel(path: string, reload: () => Promise<void>) {
    try {
      await api?.post(path);
      await reload();
    } catch (e) {
      setErr(humanError(e));
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Tasks &amp; Monitors</h1>
        <IconButton label="Refresh" onClick={() => void Promise.all([recent.reload(), schedules.reload(), monitors.reload()])}>
          <RefreshCw size={16} />
        </IconButton>
      </div>
      {err && <ErrorState message={err} />}
      <Card title="Running now">
        {running.length === 0 ? <EmptyState title="Nothing is running." icon={<ListChecks size={22} />} /> : running.map((t) => <TaskCard key={t!.taskId} task={t!} />)}
      </Card>
      <Card title="Scheduled and reminders" subtitle="Reminders and recurring tasks run even when the window is closed, while SCAR runs in the tray.">
        {schedules.loading ? (
          <Skeleton />
        ) : schedules.error ? (
          <ErrorState message={schedules.error} />
        ) : !schedules.data?.filter((s) => s.status === "active").length ? (
          <EmptyState title="Nothing scheduled." icon={<CalendarClock size={22} />}>
            Try “remind me at 5pm to call Sam” or “every weekday at 9am check the build”.
          </EmptyState>
        ) : (
          <ul className="rows">
            {schedules.data.filter((s) => s.status === "active").map((s) => (
              <li key={s.schedule_id} className="row">
                <div className="row-main">
                  <span className="row-title">{s.kind === "reminder" ? s.text : s.objective ?? s.text}</span>
                  <span className="row-sub">
                    {s.kind === "reminder" ? "Reminder" : "Task"} · next {when(s.next_run)}
                    {s.interval_seconds ? ` · repeats every ${Math.round(s.interval_seconds / 3600) || 1}h` : ""}
                  </span>
                </div>
                <Button size="sm" variant="ghost" onClick={() => void cancel(`/schedules/${s.schedule_id}/cancel`, schedules.reload)}>
                  Cancel
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Monitors" subtitle="Things SCAR is watching for you.">
        {monitors.loading ? (
          <Skeleton />
        ) : monitors.error ? (
          <ErrorState message={monitors.error} />
        ) : !monitors.data?.length ? (
          <EmptyState title="Not watching anything." icon={<Eye size={22} />}>
            Try “watch process 4412 and tell me if it crashes”.
          </EmptyState>
        ) : (
          <ul className="rows">
            {monitors.data.slice(0, 30).map((m) => (
              <li key={m.monitor_id} className="row">
                <div className="row-main">
                  <span className="row-title">{describeMonitor(m)}</span>
                  <span className="row-sub">
                    {m.status === "active" ? "Watching" : m.status === "fired" ? "Triggered" : m.status} · since {when(m.created_at)}
                  </span>
                </div>
                {m.status === "active" && (
                  <Button size="sm" variant="ghost" onClick={() => void cancel(`/monitors/${m.monitor_id}/cancel`, monitors.reload)}>
                    Stop watching
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Recent">
        {recent.loading ? (
          <Skeleton lines={5} />
        ) : recent.error ? (
          <ErrorState message={recent.error} />
        ) : !recent.data?.length ? (
          <EmptyState title="No tasks yet." />
        ) : (
          <ul className="rows">
            {recent.data.map((t) => (
              <li key={t.task_id} className="row">
                <div className="row-main">
                  <span className="row-title">{t.objective}</span>
                  <span className="row-sub">
                    {when(t.created_at)} · {t.result_summary.slice(0, 140)}
                  </span>
                </div>
                <OutcomeBadge outcome={t.status === "running" ? { kind: "running" } : outcomeFor(t.status, t.verified, t.result_summary)} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
