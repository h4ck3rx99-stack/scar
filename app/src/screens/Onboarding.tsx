// First run: skippable and resumable (progress is saved in the runtime's settings after each step).
import clsx from "clsx";
import { ArrowLeft, ArrowRight, CheckCircle2, CircleAlert, CircleX, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { humanError } from "../api/client";
import { prettyKey } from "../components/StatusBar";
import { Button, Kbd, Skeleton } from "../components/ui";
import { useApiGet, useSettings } from "../hooks/useApi";
import { openExternal } from "../lib/shell";
import { useRuntime } from "../store/runtime";
import { AutonomyPicker } from "./Permissions";

const STEPS = ["Welcome", "Folders", "Free AI", "Microphone", "Autonomy", "Accounts", "Check-up", "Done"];
export const ONBOARDING_DONE = 99;

function AiStep() {
  const api = useRuntime((s) => s.api);
  const provider = useRuntime((s) => s.provider);
  const [which, setWhich] = useState<"groq" | "gemini">("groq");
  const [key, setKey] = useState("");
  const [state, setState] = useState<{ busy: boolean; text: string; ok?: boolean }>({ busy: false, text: "" });
  const keyName = which === "groq" ? "GROQ_API_KEY" : "GEMINI_API_KEY";
  const url = which === "groq" ? "https://console.groq.com/keys" : "https://aistudio.google.com/apikey";

  async function saveAndTest() {
    setState({ busy: true, text: "" });
    try {
      await api!.put(`/secrets/${keyName}`, { value: key.trim() });
      setKey("");
      const r = await api!.post<{ ok: boolean; model?: string; latency_ms?: number; detail?: string }>(`/providers/${which}/test`);
      setState({ busy: false, ok: r.ok, text: r.ok ? `It works: answered in ${Math.round(r.latency_ms ?? 0)} ms.` : `The key was saved but the test failed: ${r.detail ?? "no answer"}` });
    } catch (e) {
      setState({ busy: false, ok: false, text: humanError(e) });
    }
  }

  return (
    <>
      <h2>Connect a free AI</h2>
      <p className="muted">
        For fast answers SCAR uses a free cloud AI. Without one it uses a local model if you have one (slower), or only basic commands. {provider.level === "cloud" && <strong>You're already connected.</strong>}
      </p>
      <div className="seg" role="radiogroup" aria-label="Provider">
        {(["groq", "gemini"] as const).map((p) => (
          <button key={p} role="radio" aria-checked={which === p} className={clsx("seg-item", which === p && "on")} onClick={() => setWhich(p)}>
            {p === "groq" ? "Groq (recommended)" : "Google Gemini"}
          </button>
        ))}
      </div>
      <ol className="steps-list">
        <li>
          <button className="link-btn" onClick={() => void openExternal(url)}>
            Open {which === "groq" ? "console.groq.com" : "aistudio.google.com"} <ExternalLink size={12} aria-hidden />
          </button>{" "}
          and create a free key.
        </li>
        <li>Paste it here. It's stored in Windows Credential Manager, not in a file.</li>
      </ol>
      <form
        className="key-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (key.trim()) void saveAndTest();
        }}
      >
        <input type="password" autoComplete="off" aria-label={`${keyName} value`} placeholder={`Paste your ${which === "groq" ? "Groq" : "Gemini"} key`} value={key} onChange={(e) => setKey(e.target.value)} />
        <Button type="submit" variant="primary" busy={state.busy} disabled={!key.trim()}>
          Save and test
        </Button>
      </form>
      {state.text && <p className={state.ok ? "text-ok" : "text-danger"}>{state.text}</p>}
    </>
  );
}

function MicStep() {
  const api = useRuntime((s) => s.api);
  const [r, setR] = useState<{ busy: boolean; text: string; ok?: boolean }>({ busy: false, text: "" });
  async function test() {
    setR({ busy: true, text: "" });
    try {
      const out = await api!.post<{ ok: boolean; heard?: string; message?: string; hint?: string }>("/voice/test", { seconds: 4, speak: true });
      setR({ busy: false, ok: out.ok, text: out.ok ? `Heard: “${out.heard || "nothing clear"}”. ${out.hint ?? ""}` : out.message ?? "The test failed." });
    } catch (e) {
      setR({ busy: false, ok: false, text: humanError(e) });
    }
  }
  return (
    <>
      <h2>Microphone (optional)</h2>
      <p className="muted">If you want to talk to SCAR, test your microphone. SCAR records 4 seconds, writes down what it heard and says it back. Skip this if you'll only type.</p>
      <Button busy={r.busy} onClick={() => void test()}>
        {r.busy ? "Recording… say something" : "Test microphone"}
      </Button>
      {r.text && <p className={r.ok ? "text-ok" : "text-danger"}>{r.text}</p>}
    </>
  );
}

function DoctorStep() {
  const q = useApiGet<{ ok: boolean | null; label: string; hint: string }[]>("/doctor");
  if (q.loading) return <Skeleton lines={5} />;
  const checks = q.data ?? [];
  const bad = checks.filter((c) => c.ok === false);
  const warn = checks.filter((c) => c.ok === null);
  return (
    <>
      <h2>Check-up</h2>
      <p className="muted">
        {checks.filter((c) => c.ok).length} checks passed{warn.length ? `, ${warn.length} notes` : ""}
        {bad.length ? `, ${bad.length} problems` : ""}.
      </p>
      <ul className="checks">
        {[...bad, ...warn].slice(0, 8).map((c, i) => (
          <li key={i} className={clsx("check", c.ok === false ? "bad" : "warn")}>
            {c.ok === false ? <CircleX size={15} aria-hidden /> : <CircleAlert size={15} aria-hidden />}
            <span className="check-label">{c.label}</span>
            {c.hint && <span className="check-hint">{c.hint}</span>}
          </li>
        ))}
        {!bad.length && !warn.length && (
          <li className="check ok">
            <CheckCircle2 size={15} aria-hidden /> Everything looks good.
          </li>
        )}
      </ul>
      <p className="muted small">You can see the full list any time under Diagnostics.</p>
    </>
  );
}

export function Onboarding({ onDone }: { onDone: () => void }) {
  const s = useSettings();
  const [step, setStep] = useState<number | null>(null);
  const [folder, setFolder] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!s.loading && step === null) setStep(Math.min(STEPS.length - 1, Math.max(0, s.value("app_onboarding_step", 0))));
  }, [s, step]);

  if (s.loading || step === null) {
    return (
      <div className="onboarding">
        <Skeleton lines={4} />
      </div>
    );
  }

  async function goTo(n: number) {
    setErr("");
    const e = await s.save({ app_onboarding_step: n >= STEPS.length ? ONBOARDING_DONE : n });
    if (e) setErr(e);
    if (n >= STEPS.length) onDone();
    else setStep(n);
  }

  const roots = s.value<string[]>("allowed_roots", []);
  return (
    <div className="onboarding" role="dialog" aria-label="Set up SCAR">
      <div className="onboarding-card">
        <ol className="stepper" aria-label="Setup progress">
          {STEPS.map((name, i) => (
            <li key={name} className={clsx(i === step && "current", i < step && "done")} aria-current={i === step ? "step" : undefined}>
              <span className="sr-only">{name}</span>
            </li>
          ))}
        </ol>
        <div className="onboarding-body">
          {step === 0 && (
            <>
              <img src="/scar-mark.svg" alt="" className="welcome-mark" />
              <h2>Welcome to SCAR</h2>
              <p className="muted">SCAR operates your computer for you: it opens apps and projects, works with files, runs and fixes tests, browses, researches, and keeps an eye on things. It checks that each action really worked, and it asks before anything risky.</p>
              <p className="muted">Setup takes about two minutes. You can skip any step and come back later in Settings.</p>
            </>
          )}
          {step === 1 && (
            <>
              <h2>Where may SCAR work?</h2>
              <p className="muted">SCAR only reads and changes files inside these folders. Leave the list empty to allow your whole user folder. System folders and credential stores are always off-limits.</p>
              <ul className="rows">
                {roots.length === 0 && <li className="row">Your user folder</li>}
                {roots.map((r) => (
                  <li key={r} className="row">
                    <span className="mono">{r}</span>
                    <Button size="sm" variant="ghost" onClick={() => void s.save({ allowed_roots: roots.filter((x) => x !== r) })}>
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
              <form
                className="inline-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (folder.trim()) void s.save({ allowed_roots: [...roots, folder.trim()] }).then((x) => (x ? setErr(x) : setFolder("")));
                }}
              >
                <input aria-label="Folder path" placeholder="C:\Users\you\Projects" value={folder} onChange={(e) => setFolder(e.target.value)} />
                <Button type="submit" disabled={!folder.trim()}>
                  Add folder
                </Button>
              </form>
            </>
          )}
          {step === 2 && <AiStep />}
          {step === 3 && <MicStep />}
          {step === 4 && (
            <>
              <h2>How much should SCAR do on its own?</h2>
              <AutonomyPicker value={s.value("autonomy_level", 3)} onChange={(n) => void s.save({ autonomy_level: n })} />
            </>
          )}
          {step === 5 && (
            <>
              <h2>Accounts (optional)</h2>
              <p className="muted">SCAR can read and send email, messages and calendar events once you connect an account. It always shows you exactly what it will send, and to whom, before sending. Connect accounts any time in Settings → Accounts.</p>
            </>
          )}
          {step === 6 && <DoctorStep />}
          {step === 7 && (
            <>
              <h2>You're set</h2>
              <p className="muted">
                Press <Kbd>{s.value("quickbar_hotkey", "Ctrl+Alt+Space")}</Kbd> anywhere to open the Quick Bar. Closing the window keeps SCAR in the tray. {prettyKey(s.value("kill_switch_hotkey", "")) && <>Emergency stop: <Kbd>{prettyKey(s.value("kill_switch_hotkey", ""))}</Kbd>.</>}
              </p>
            </>
          )}
          {err && <p className="text-danger">{err}</p>}
        </div>
        <div className="onboarding-actions">
          {step > 0 && (
            <Button variant="ghost" icon={<ArrowLeft size={15} aria-hidden />} onClick={() => void goTo(step - 1)}>
              Back
            </Button>
          )}
          <span className="spacer" />
          {step < STEPS.length - 1 && (
            <Button variant="ghost" onClick={() => void goTo(STEPS.length)}>
              Skip setup
            </Button>
          )}
          <Button variant="primary" icon={step < STEPS.length - 1 ? <ArrowRight size={15} aria-hidden /> : undefined} onClick={() => void goTo(step + 1)}>
            {step === 0 ? "Get started" : step < STEPS.length - 1 ? "Next" : "Start using SCAR"}
          </Button>
        </div>
      </div>
    </div>
  );
}
