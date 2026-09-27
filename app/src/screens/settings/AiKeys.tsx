// AI providers and API keys. Keys go straight to the runtime, which stores them in Windows Credential Manager;
// the app never receives a key back, only "set" or "not set".
import clsx from "clsx";
import { ExternalLink, KeyRound, PlugZap } from "lucide-react";
import { useState } from "react";
import { humanError } from "../../api/client";
import { Badge, Button, Card, ErrorState, Skeleton } from "../../components/ui";
import { useApiGet } from "../../hooks/useApi";
import { openExternal } from "../../lib/shell";
import { useRuntime } from "../../store/runtime";

interface Provider { id: string; tier: string; local: boolean; configured: boolean; needs: string; key_names: string[]; data_terms: string; setup_doc: string; health: { model: string; state: string; last_error: string }[] }
interface Secret { name: string; set: boolean; source: string }

export const KEY_LINKS: Record<string, { label: string; url: string; blurb: string }> = {
  groq: { label: "Groq", url: "https://console.groq.com/keys", blurb: "Free, very fast. Recommended." },
  gemini: { label: "Google Gemini", url: "https://aistudio.google.com/apikey", blurb: "Free tier; also reads screenshots." },
  cerebras: { label: "Cerebras", url: "https://cloud.cerebras.ai", blurb: "Free trial credits, very fast." },
  openrouter: { label: "OpenRouter", url: "https://openrouter.ai/keys", blurb: "Many free models behind one key." },
  mistral: { label: "Mistral", url: "https://console.mistral.ai/api-keys", blurb: "Free experiment tier." },
  nvidia: { label: "NVIDIA", url: "https://build.nvidia.com", blurb: "Free developer credits." },
  cloudflare: { label: "Cloudflare Workers AI", url: "https://dash.cloudflare.com", blurb: "Small free daily allowance." },
  huggingface: { label: "Hugging Face", url: "https://huggingface.co/settings/tokens", blurb: "Small monthly free credit." },
  anthropic: { label: "Anthropic (paid)", url: "https://console.anthropic.com", blurb: "Paid; used only if you add a key." },
  openai: { label: "OpenAI (paid)", url: "https://platform.openai.com/api-keys", blurb: "Paid; used only if you add a key." },
};

const EXTRA_KEYS = [
  { name: "BRAVE_API_KEY", label: "Brave Search", url: "https://api-dashboard.search.brave.com", blurb: "Better web search than the free fallback." },
  { name: "TAVILY_API_KEY", label: "Tavily", url: "https://app.tavily.com", blurb: "Web search built for AI research." },
  { name: "AZURE_SPEECH_KEY", label: "Azure Speech", url: "https://portal.azure.com", blurb: "Natural-sounding voice output." },
];

function KeyForm({ name, isSet, onSaved }: { name: string; isSet: boolean; onSaved: () => Promise<void> }) {
  const api = useRuntime((s) => s.api);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  async function save() {
    setBusy(true);
    setMsg("");
    try {
      await api?.put(`/secrets/${name}`, { value: value.trim() });
      setValue("");
      setMsg("Saved to Windows Credential Manager.");
      await onSaved();
    } catch (e) {
      setMsg(humanError(e));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    try {
      await api?.del(`/secrets/${name}`);
      await onSaved();
    } catch (e) {
      setMsg(humanError(e));
    }
  }
  return (
    <form
      className="key-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) void save();
      }}
    >
      <input type="password" autoComplete="off" spellCheck={false} aria-label={`${name} value`} placeholder={isSet ? "•••••••• saved — paste a new key to replace it" : `Paste your ${name}`} value={value} onChange={(e) => setValue(e.target.value)} />
      <Button type="submit" variant="primary" size="sm" busy={busy} disabled={!value.trim()} icon={<KeyRound size={14} aria-hidden />}>
        Save key
      </Button>
      {isSet && (
        <Button type="button" variant="ghost" size="sm" onClick={() => void remove()}>
          Remove
        </Button>
      )}
      {msg && <span className="key-msg">{msg}</span>}
    </form>
  );
}

export function AiKeys() {
  const providers = useApiGet<{ providers: Provider[]; summary: { level: string; summary: string } }>("/providers");
  const secrets = useApiGet<Secret[]>("/secrets");
  const api = useRuntime((s) => s.api);
  const [tests, setTests] = useState<Record<string, { busy: boolean; text: string; ok?: boolean }>>({});
  const [showAll, setShowAll] = useState(false);
  const secretSet = (n: string) => Boolean(secrets.data?.find((s) => s.name === n)?.set);

  async function test(id: string) {
    setTests((t) => ({ ...t, [id]: { busy: true, text: "" } }));
    try {
      const r = await api!.post<{ ok: boolean; model?: string; latency_ms?: number; detail?: string }>(`/providers/${id}/test`);
      setTests((t) => ({ ...t, [id]: { busy: false, ok: r.ok, text: r.ok ? `Works: ${r.model ?? ""} answered in ${Math.round(r.latency_ms ?? 0)} ms` : r.detail ?? "It didn't answer." } }));
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { busy: false, ok: false, text: humanError(e) } }));
    }
  }

  const reload = async () => {
    await Promise.all([providers.reload(), secrets.reload()]);
  };

  if (providers.loading || secrets.loading) return <Skeleton lines={6} />;
  if (providers.error) return <ErrorState message={providers.error} />;
  const cloud = (providers.data?.providers ?? []).filter((p) => !p.local && KEY_LINKS[p.id]);
  const featured = cloud.filter((p) => p.id === "groq" || p.id === "gemini" || p.configured);
  const rest = cloud.filter((p) => !featured.includes(p));

  const card = (p: Provider) => {
    const link = KEY_LINKS[p.id]!;
    const keyName = p.key_names[0] ?? "";
    const t = tests[p.id];
    const bad = p.health.find((h) => h.state === "unavailable" || h.state === "open");
    return (
      <div key={p.id} className="provider">
        <div className="provider-head">
          <span className="provider-name">{link.label}</span>
          {p.configured ? bad ? <Badge tone="warn">Key saved · {bad.state === "open" ? "having trouble" : "not working"}</Badge> : <Badge tone="ok">Connected</Badge> : <Badge>Not set up</Badge>}
          <span className="spacer" />
          <button className="link-btn" onClick={() => void openExternal(link.url)}>
            Get a key <ExternalLink size={12} aria-hidden />
          </button>
        </div>
        <p className="muted small">
          {link.blurb} {p.data_terms}
        </p>
        {keyName && <KeyForm name={keyName} isSet={secretSet(keyName)} onSaved={reload} />}
        {p.configured && (
          <div className="provider-test">
            <Button size="sm" busy={t?.busy} icon={<PlugZap size={14} aria-hidden />} onClick={() => void test(p.id)}>
              Test with a real call
            </Button>
            {t?.text && <span className={clsx("small", t.ok ? "text-ok" : "text-danger")}>{t.text}</span>}
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <Card title="AI connection" subtitle={providers.data?.summary.summary}>
        <p className="muted small">SCAR uses free cloud AI first, then your local model (if set up), then basic commands. Add one free key for fast answers.</p>
        <div className="providers">{featured.map(card)}</div>
        {rest.length > 0 && (
          <>
            <button className="link-btn" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
              {showAll ? "Hide other providers" : `Show ${rest.length} more providers`}
            </button>
            {showAll && <div className="providers">{rest.map(card)}</div>}
          </>
        )}
      </Card>
      <Card title="Search and speech keys" subtitle="Optional. Without them SCAR uses free fallbacks.">
        <div className="providers">
          {EXTRA_KEYS.map((k) => (
            <div key={k.name} className="provider">
              <div className="provider-head">
                <span className="provider-name">{k.label}</span>
                {secretSet(k.name) ? <Badge tone="ok">Saved</Badge> : <Badge>Not set</Badge>}
                <span className="spacer" />
                <button className="link-btn" onClick={() => void openExternal(k.url)}>
                  Get a key <ExternalLink size={12} aria-hidden />
                </button>
              </div>
              <p className="muted small">{k.blurb}</p>
              <KeyForm name={k.name} isSet={secretSet(k.name)} onSaved={reload} />
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
