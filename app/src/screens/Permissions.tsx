import clsx from "clsx";
import { FolderPlus, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { humanError } from "../api/client";
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useApiGet, useSettings } from "../hooks/useApi";
import { useRuntime } from "../store/runtime";

export const AUTONOMY = [
  { level: 0, name: "Conversation only", text: "SCAR talks but never acts." },
  { level: 1, name: "Suggest", text: "SCAR proposes a plan and acts on nothing." },
  { level: 2, name: "Careful", text: "Low-risk actions (reading, listing, looking) run on their own; everything else asks first." },
  { level: 3, name: "Balanced (recommended)", text: "Low and medium-risk actions run on their own (opening apps, writing new files, running tests). High-risk ones ask first (deleting, sending, installing)." },
  { level: 4, name: "Trusted", text: "High-risk actions also run when they match a permission you saved. Critical actions always ask." },
];

interface Grant { grant_id: string; effect: string; kind: string; tool: string | null; match: Record<string, unknown>; scope: string; expires_at: string | null; created_at: string; uses: number; max_risk: number | string }
interface AuditRow { seq: number; at: string; kind: string; detail_json: string; task_id: string | null }

function grantText(g: Grant): string {
  const what = g.kind === "tool" ? `every “${g.tool}” action` : g.kind === "exact" ? `this exact “${g.tool}” action` : `${g.kind} ${Object.values(g.match).join(", ")}`;
  const scope = g.scope === "persistent" ? "always" : g.scope === "session" ? "until SCAR restarts" : g.scope === "timed" ? `until ${g.expires_at ? new Date(g.expires_at).toLocaleTimeString() : "it expires"}` : `for ${g.scope}`;
  return `${g.effect === "deny" ? "Never allow" : "Allow"} ${what}, ${scope}`;
}

export function AutonomyPicker({ value, onChange, disabled }: { value: number; onChange: (n: number) => void; disabled?: boolean }) {
  return (
    <div className="radio-cards" role="radiogroup" aria-label="Autonomy level">
      {AUTONOMY.map((a) => (
        <button key={a.level} role="radio" aria-checked={value === a.level} disabled={disabled} className={clsx("radio-card", value === a.level && "selected")} onClick={() => onChange(a.level)}>
          <span className="radio-card-title">
            {a.level} · {a.name}
          </span>
          <span className="radio-card-text">{a.text}</span>
        </button>
      ))}
    </div>
  );
}

export function Permissions() {
  const settings = useSettings();
  const grants = useApiGet<Grant[]>("/grants");
  const audit = useApiGet<AuditRow[]>("/audit?limit=100");
  const api = useRuntime((s) => s.api);
  const [err, setErr] = useState("");
  const [folder, setFolder] = useState("");
  const roots = settings.value<string[]>("allowed_roots", []);

  async function saveRoots(next: string[]) {
    const e = await settings.save({ allowed_roots: next });
    setErr(e ?? "");
  }

  async function revoke(id?: string) {
    try {
      await api?.del(id ? `/grants/${id}` : "/grants");
      await grants.reload();
    } catch (e) {
      setErr(humanError(e));
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Permissions</h1>
      </div>
      {err && <ErrorState message={err} />}
      <Card title="How much SCAR does on its own" subtitle="Critical actions (payments, permanent deletion, protected files) always need you to type a confirmation word, at every level.">
        {settings.loading ? <Skeleton /> : <AutonomyPicker value={settings.value("autonomy_level", 3)} onChange={(n) => void settings.save({ autonomy_level: n }).then((e) => setErr(e ?? ""))} />}
      </Card>
      <Card
        title="Saved permissions"
        subtitle="Created when you choose “Always…” or “Allow for…” on an approval."
        actions={grants.data?.length ? <Button size="sm" variant="ghost" onClick={() => void revoke()}>Revoke all</Button> : undefined}
      >
        {grants.loading ? (
          <Skeleton />
        ) : grants.error ? (
          <ErrorState message={grants.error} />
        ) : !grants.data?.length ? (
          <EmptyState title="No saved permissions." icon={<ShieldCheck size={22} />}>SCAR asks each time for anything above your autonomy level.</EmptyState>
        ) : (
          <ul className="rows">
            {grants.data.map((g) => (
              <li key={g.grant_id} className="row">
                <div className="row-main">
                  <span className="row-title">{grantText(g)}</span>
                  <span className="row-sub">
                    Added {new Date(g.created_at).toLocaleString()} · used {g.uses} time{g.uses === 1 ? "" : "s"}
                  </span>
                </div>
                <Button size="sm" variant="ghost" icon={<Trash2 size={14} aria-hidden />} onClick={() => void revoke(g.grant_id)}>
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Folders SCAR may work in" subtitle="SCAR can read and change files only inside these folders (your user folder when the list is empty). System folders, other users and credential stores are always off-limits.">
        {settings.loading ? (
          <Skeleton />
        ) : (
          <>
            <ul className="rows">
              {roots.length === 0 && (
                <li className="row">
                  <span className="row-title">Your user folder (default)</span>
                </li>
              )}
              {roots.map((r) => (
                <li key={r} className="row">
                  <span className="row-title mono">{r}</span>
                  <Button size="sm" variant="ghost" onClick={() => void saveRoots(roots.filter((x) => x !== r))}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                const f = folder.trim();
                if (f && !roots.includes(f)) void saveRoots([...roots, f]).then(() => setFolder(""));
              }}
            >
              <input aria-label="Folder path" placeholder="C:\Users\you\Projects" value={folder} onChange={(e) => setFolder(e.target.value)} />
              <Button type="submit" icon={<FolderPlus size={15} aria-hidden />} disabled={!folder.trim()}>
                Add folder
              </Button>
            </form>
          </>
        )}
      </Card>
      <Card title="Audit log" subtitle="Every permission decision, setting change and sensitive action, in an append-only log.">
        {audit.loading ? (
          <Skeleton lines={4} />
        ) : audit.error ? (
          <ErrorState message={audit.error} />
        ) : !audit.data?.length ? (
          <EmptyState title="Nothing logged yet." />
        ) : (
          <ul className="rows compact">
            {audit.data.map((a) => (
              <li key={a.seq} className="row">
                <span className="row-sub mono">{new Date(a.at).toLocaleString()}</span>
                <Badge>{a.kind.replace(/_/g, " ")}</Badge>
                <span className="row-sub truncate">{a.detail_json}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
