import { CheckCircle2, CircleDashed, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { humanError } from "../../api/client";
import { Badge, Button, Card, ErrorState, Field, Skeleton } from "../../components/ui";
import { useApiGet, useSettings } from "../../hooks/useApi";
import { useRuntime } from "../../store/runtime";

interface Account { name: string; label: string; provides: string; connected: boolean; needs: string[]; connect: "oauth" | "key" | "settings" | "terminal"; doc: string }
interface Job { name: string; state: string; message: string }

// non-secret settings each account needs, edited right here
const FIELDS: Record<string, { name: string; label: string; placeholder: string }[]> = {
  google: [{ name: "google_oauth_client_file", label: "OAuth client file (Desktop app JSON)", placeholder: "C:\\Users\\you\\client_secret.json" }],
  microsoft: [{ name: "ms_client_id", label: "Application (client) ID", placeholder: "00000000-0000-0000-0000-000000000000" }],
  imap: [
    { name: "imap_host", label: "IMAP server", placeholder: "imap.example.com" },
    { name: "imap_user", label: "User name", placeholder: "you@example.com" },
    { name: "smtp_host", label: "SMTP server", placeholder: "smtp.example.com" },
  ],
  telegram_user: [{ name: "telegram_api_id", label: "api_id (from my.telegram.org)", placeholder: "1234567" }],
  whatsapp: [{ name: "whatsapp_phone_number_id", label: "Cloud API phone number ID (optional)", placeholder: "" }],
};

const KEY_OF: Record<string, string> = { telegram: "TELEGRAM_BOT_TOKEN", discord: "DISCORD_BOT_TOKEN", github: "GITHUB_TOKEN", imap: "SCAR_IMAP_PASSWORD", telegram_user: "TELEGRAM_API_HASH", whatsapp: "WHATSAPP_CLOUD_TOKEN" };

function AccountRow({ a, jobs, reload }: { a: Account; jobs: Record<string, Job>; reload: () => Promise<void> }) {
  const api = useRuntime((s) => s.api);
  const settings = useSettings();
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [key, setKey] = useState("");
  const job = jobs[a.name];

  async function run(path: string) {
    setBusy(true);
    setMsg("");
    try {
      const r = await api!.post<Job | { removed: string[] }>(path);
      if ("message" in r) setMsg(r.message);
      else setMsg(r.removed.length ? "Disconnected." : "Nothing to remove.");
      await reload();
    } catch (e) {
      setMsg(humanError(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveFields() {
    const values = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim()));
    if (key.trim() && KEY_OF[a.name]) {
      try {
        await api!.put(`/secrets/${KEY_OF[a.name]}`, { value: key.trim() });
        setKey("");
      } catch (e) {
        setMsg(humanError(e));
        return;
      }
    }
    if (Object.keys(values).length) {
      const e = await settings.save(values);
      if (e) {
        setMsg(e);
        return;
      }
    }
    setMsg("Saved.");
    setVals({});
    await reload();
  }

  const fields = FIELDS[a.name] ?? [];
  return (
    <div className="account">
      <div className="account-head">
        {a.connected ? <CheckCircle2 size={18} className="text-ok" aria-hidden /> : <CircleDashed size={18} className="muted" aria-hidden />}
        <div>
          <span className="account-name">{a.label}</span>
          <span className="muted small"> · {a.provides}</span>
        </div>
        <span className="spacer" />
        {a.connected ? <Badge tone="ok">Connected</Badge> : <Badge>Not connected</Badge>}
      </div>
      {!a.connected && a.needs.length > 0 && <p className="needs">Needs: {a.needs.join("; ")}.</p>}
      {(fields.length > 0 || KEY_OF[a.name]) && !a.connected && (
        <div className="account-fields">
          {fields.map((f) => (
            <Field key={f.name} label={f.label} htmlFor={`${a.name}-${f.name}`}>
              <input id={`${a.name}-${f.name}`} placeholder={String(settings.byName[f.name]?.value || f.placeholder)} value={vals[f.name] ?? ""} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
            </Field>
          ))}
          {KEY_OF[a.name] && (
            <Field label={KEY_OF[a.name]!} htmlFor={`${a.name}-key`} hint="Stored in Windows Credential Manager; never shown again.">
              <input id={`${a.name}-key`} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
          )}
          <Button size="sm" onClick={() => void saveFields()} disabled={!key.trim() && !Object.values(vals).some((v) => v.trim())}>
            Save
          </Button>
        </div>
      )}
      <div className="account-actions">
        {a.connect === "oauth" && (
          <Button size="sm" variant="primary" busy={busy || job?.state === "waiting_for_browser"} disabled={a.needs.some((n) => n !== "sign-in")} onClick={() => void run(`/accounts/${a.name}/connect`)}>
            {a.connected ? "Sign in again" : "Sign in with your browser"}
          </Button>
        )}
        {a.connect === "key" && a.connected && (
          <Button size="sm" busy={busy} onClick={() => void run(`/accounts/${a.name}/connect`)}>
            Check connection
          </Button>
        )}
        {a.connected && (
          <Button size="sm" variant="ghost" onClick={() => void run(`/accounts/${a.name}/disconnect`)}>
            Disconnect
          </Button>
        )}
        {a.doc && <span className="muted small">Guide: {a.doc}</span>}
      </div>
      {(msg || job?.message) && <p className="small account-msg">{msg || job?.message}</p>}
    </div>
  );
}

export function Accounts() {
  const q = useApiGet<{ accounts: Account[]; jobs: Record<string, Job> }>("/accounts");
  // a browser sign-in finishes in the runtime and announces itself; refresh when it does
  const lastNotice = useRuntime((s) => s.notices[s.notices.length - 1]?.text ?? "");
  const { reload } = q;
  useEffect(() => {
    if (/^(google|microsoft): /.test(lastNotice)) void reload();
  }, [lastNotice, reload]);
  if (q.loading) return <Skeleton lines={6} />;
  if (q.error) return <ErrorState message={q.error} />;
  return (
    <Card title="Accounts" subtitle="Connect only what you want SCAR to use. SCAR never sends a message or email without showing you exactly what, to whom, and asking.">
      <p className="muted small">
        Sign-ins open your default browser <ExternalLink size={11} aria-hidden /> and return here automatically. Passwords and tokens are stored in Windows Credential Manager.
      </p>
      <div className="accounts">
        {q.data!.accounts.map((a) => (
          <AccountRow key={a.name} a={a} jobs={q.data!.jobs} reload={q.reload} />
        ))}
      </div>
    </Card>
  );
}
