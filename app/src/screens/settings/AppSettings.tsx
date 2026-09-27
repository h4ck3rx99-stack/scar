import { useEffect, useState } from "react";
import { prettyKey } from "../../components/StatusBar";
import { Button, Card, ErrorState, Field, Skeleton, Switch } from "../../components/ui";
import { useSettings, type SettingField } from "../../hooks/useApi";
import { appVersion, autostartEnabled, isTauri, openExternal, registerQuickBarShortcut, setAutostart } from "../../lib/shell";

function Row({ title, text, children }: { title: string; text: string; children: React.ReactNode }) {
  return (
    <div className="setting-row">
      <div>
        <p className="setting-title">{title}</p>
        <p className="muted small">{text}</p>
      </div>
      {children}
    </div>
  );
}

export function AppSettings() {
  const s = useSettings();
  const [err, setErr] = useState("");
  const [hotkeyMsg, setHotkeyMsg] = useState("");
  const [autostart, setAuto] = useState(false);
  const [version, setVersion] = useState("");
  const [exported, setExported] = useState("");
  const [importText, setImportText] = useState("");

  useEffect(() => {
    void autostartEnabled().then(setAuto);
    void appVersion().then(setVersion);
  }, []);

  async function save(values: Record<string, unknown>) {
    setErr((await s.save(values)) ?? "");
  }

  async function saveHotkey(value: string) {
    const failure = await registerQuickBarShortcut(value);
    if (failure) {
      setHotkeyMsg(`Windows wouldn't give SCAR that shortcut (another app may be using it): ${failure}`);
      return;
    }
    setHotkeyMsg("Shortcut updated.");
    await save({ quickbar_hotkey: value });
  }

  function exportSettings() {
    // non-secret settings only: the runtime never returns keys, so they cannot end up in an export
    const values = Object.fromEntries(s.fields.filter((f: SettingField) => f.value !== f.default && f.name !== "app_onboarding_step").map((f) => [f.name, f.value]));
    setExported(JSON.stringify(values, null, 2));
    void navigator.clipboard?.writeText(JSON.stringify(values, null, 2)).catch(() => undefined);
  }

  async function importSettings() {
    let values: Record<string, unknown>;
    try {
      values = JSON.parse(importText) as Record<string, unknown>;
    } catch {
      setErr("That isn't valid JSON. Paste the text you exported.");
      return;
    }
    await save(values);
    if (!err) setImportText("");
  }

  if (s.loading) return <Skeleton lines={6} />;
  return (
    <>
      {err && <ErrorState message={err} />}
      <Card title="Appearance">
        <Field label="Theme" htmlFor="theme">
          <select id="theme" value={s.value("theme", "system")} onChange={(e) => void save({ theme: e.target.value })}>
            <option value="system">Follow Windows</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </Field>
      </Card>
      <Card title="Shortcuts">
        <Field label="Quick Bar" htmlFor="qb-key" hint={hotkeyMsg || "Opens the Quick Bar from anywhere. Examples: Ctrl+Alt+Space, Ctrl+Shift+S."}>
          <input id="qb-key" defaultValue={s.value("quickbar_hotkey", "Ctrl+Alt+Space")} onBlur={(e) => e.target.value !== s.value("quickbar_hotkey", "") && void saveHotkey(e.target.value)} />
        </Field>
        <p className="muted small">Emergency stop (halts input, cancels tasks, stops speech): {prettyKey(s.value("kill_switch_hotkey", ""))}</p>
      </Card>
      <Card title="Startup and background">
        <Row title="Launch SCAR when I sign in to Windows" text="Starts minimized in the tray so reminders and monitors keep working.">
          <Switch label="Launch at sign-in" checked={autostart} disabled={!isTauri()} onChange={(v) => void setAutostart(v).then(setAuto)} />
        </Row>
        <Row title="Start minimized" text="Open to the tray instead of the window.">
          <Switch label="Start minimized" checked={s.value("start_minimized", false)} onChange={(v) => void save({ start_minimized: v })} />
        </Row>
        <Row title="Keep SCAR running after “Quit”" text="Quit closes the window and tray but leaves the background runtime on for reminders and monitors.">
          <Switch label="Keep running in background" checked={s.value("keep_running_in_background", false)} onChange={(v) => void save({ keep_running_in_background: v })} />
        </Row>
      </Card>
      <Card title="While you're busy">
        <Row title="Game and presentation mode" text="While a full-screen game, video or presentation is in front: notifications and speech wait, and background tasks don't take over the screen or start local AI.">
          <Switch label="Game mode" checked={s.value("game_mode", "auto") === "auto"} onChange={(v) => void save({ game_mode: v ? "auto" : "off" })} />
        </Row>
        <Row title="Show when SCAR controls the computer" text="A banner at the top of the screen while SCAR moves the mouse, types or switches windows, with the stop shortcut.">
          <Switch label="Control indicator" checked={s.value("control_indicator", true)} onChange={(v) => void save({ control_indicator: v })} />
        </Row>
      </Card>
      <Card title="Export and import settings" subtitle="Copies your non-secret settings as text. API keys and sign-ins are never included.">
        <div className="button-row">
          <Button onClick={exportSettings}>Export (copies to clipboard)</Button>
        </div>
        {exported && <pre className="export-box">{exported}</pre>}
        <textarea className="import-box" aria-label="Settings to import" placeholder="Paste exported settings here" value={importText} onChange={(e) => setImportText(e.target.value)} rows={4} />
        <Button disabled={!importText.trim()} onClick={() => void importSettings()}>
          Import
        </Button>
      </Card>
      <Card title="About SCAR">
        <p>
          SCAR — Systemic Cognitive Autonomous Responder · version {version || "…"}
        </p>
        <p className="muted small">
          Third-party components and their licenses are listed in THIRD_PARTY_NOTICES.md (installed next to SCAR).{" "}
          <button className="link-btn" onClick={() => void openExternal("https://tauri.app")}>
            Built with Tauri
          </button>
        </p>
      </Card>
    </>
  );
}

export function AdvancedSettings() {
  const s = useSettings();
  const [err, setErr] = useState("");
  if (s.loading) return <Skeleton lines={8} />;
  const groups = new Map<string, SettingField[]>();
  for (const f of s.fields) {
    if (f.group === "App" || f.group === "Voice") continue;
    groups.set(f.group, [...(groups.get(f.group) ?? []), f]);
  }
  async function save(name: string, raw: unknown) {
    setErr((await s.save({ [name]: raw })) ?? "");
  }
  return (
    <>
      <p className="muted page-intro">Every other setting. Changes are validated by SCAR and saved to %APPDATA%\SCAR\config.toml.</p>
      {err && <ErrorState message={err} />}
      {[...groups.entries()].map(([group, fields]) => (
        <Card key={group} title={group}>
          <div className="adv-list">
            {fields.map((f) => (
              <div key={f.name} className="adv-row">
                <label htmlFor={`adv-${f.name}`} className="adv-name">
                  <span className="mono">{f.name}</span>
                  <span className="muted small">{f.description}</span>
                </label>
                {f.kind === "bool" ? (
                  <Switch id={`adv-${f.name}`} label={f.name} checked={Boolean(f.value)} onChange={(v) => void save(f.name, v)} />
                ) : f.kind === "choice" ? (
                  <select id={`adv-${f.name}`} value={String(f.value)} onChange={(e) => void save(f.name, e.target.value)}>
                    {f.choices.map((c) => (
                      <option key={String(c)} value={String(c)}>
                        {String(c)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={`adv-${f.name}`}
                    defaultValue={Array.isArray(f.value) ? (f.value as unknown[]).join(", ") : String(f.value ?? "")}
                    onBlur={(e) => {
                      const cur = Array.isArray(f.value) ? (f.value as unknown[]).join(", ") : String(f.value ?? "");
                      if (e.target.value !== cur) void save(f.name, e.target.value);
                    }}
                  />
                )}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </>
  );
}
