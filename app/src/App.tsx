import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { Brain, ListChecks, MessageSquare, RefreshCw, Settings as SettingsIcon, ShieldCheck, Stethoscope } from "lucide-react";
import { useEffect, useRef } from "react";
import { StatusBar } from "./components/StatusBar";
import { Button } from "./components/ui";
import { useSettings } from "./hooks/useApi";
import { mainWindowVisible, onShellEvent, restartRuntime, setIndicator, setTrayState, showMainWindow, type TrayState } from "./lib/shell";
import { Conversation } from "./screens/Conversation";
import { Diagnostics } from "./screens/Diagnostics";
import { MemoryScreen } from "./screens/Memory";
import { Onboarding, ONBOARDING_DONE } from "./screens/Onboarding";
import { Permissions } from "./screens/Permissions";
import { Settings } from "./screens/settings/Settings";
import { Tasks } from "./screens/Tasks";
import { runningTaskIds } from "./store/reducer";
import { useRuntime } from "./store/runtime";
import { useUi, type Route } from "./store/ui";

const NAV: { id: Route; label: string; icon: React.ReactNode }[] = [
  { id: "conversation", label: "Conversation", icon: <MessageSquare size={18} aria-hidden /> },
  { id: "tasks", label: "Tasks & Monitors", icon: <ListChecks size={18} aria-hidden /> },
  { id: "permissions", label: "Permissions", icon: <ShieldCheck size={18} aria-hidden /> },
  { id: "memory", label: "Memory", icon: <Brain size={18} aria-hidden /> },
  { id: "settings", label: "Settings", icon: <SettingsIcon size={18} aria-hidden /> },
  { id: "diagnostics", label: "Diagnostics", icon: <Stethoscope size={18} aria-hidden /> },
];

function ConnectionBanner() {
  const conn = useRuntime((s) => s.conn);
  const msg = useRuntime((s) => s.connMessage);
  const connect = useRuntime((s) => s.connect);
  const go = useUi((s) => s.go);
  if (conn === "online") return null;
  const starting = conn === "starting" || conn === "connecting";
  return (
    <div className={clsx("conn-banner", starting ? "info" : "bad")} role="status" aria-live="polite">
      <span>{starting ? msg || "Starting SCAR's runtime…" : msg || "SCAR's runtime isn't running."}</span>
      {!starting && (
        <>
          <Button size="sm" icon={<RefreshCw size={13} aria-hidden />} onClick={() => void restartRuntime().then(() => connect())}>
            Restart runtime
          </Button>
          <Button size="sm" variant="ghost" onClick={() => go("diagnostics")}>
            Open diagnostics
          </Button>
        </>
      )}
    </div>
  );
}

/** Keeps the tray icon, the screen-control indicator and approval toasts in step with the runtime. */
function ShellSync() {
  const assistant = useRuntime((s) => s.assistant);
  const conn = useRuntime((s) => s.conn);
  const approvals = useRuntime((s) => s.approvals);
  const control = useRuntime((s) => s.control);
  const killKey = useRuntime((s) => s.killKey);
  const openAppAt = useRuntime((s) => s.openAppAt);
  const api = useRuntime((s) => s.api);
  const voice = useRuntime((s) => s.voice);
  const notified = useRef(new Set<string>());
  const settings = useSettings();
  const indicatorOn = settings.value("control_indicator", true);

  useEffect(() => {
    let state: TrayState = "idle";
    let tip = "SCAR — ready";
    if (conn !== "online") [state, tip] = ["error", "SCAR — runtime not running"];
    else if (Object.keys(approvals).length) [state, tip] = ["approval", "SCAR — needs your approval"];
    else if (voice.state === "listening") [state, tip] = ["listening", "SCAR — listening"];
    else if (assistant === "working" || assistant === "speaking") [state, tip] = ["working", "SCAR — working"];
    void setTrayState(state, tip);
  }, [assistant, conn, approvals, voice.state]);

  useEffect(() => {
    void setIndicator(indicatorOn && control.active, control.what, killKey);
  }, [control, killKey, indicatorOn]);

  useEffect(() => {
    // approval while the window is hidden: a toast that only opens SCAR (nothing can be approved from a toast)
    const fresh = Object.keys(approvals).filter((id) => !notified.current.has(id));
    if (!fresh.length || !api) return;
    fresh.forEach((id) => notified.current.add(id));
    void mainWindowVisible().then((visible) => {
      if (!visible) void api.post("/notify-approval", {}).catch(() => undefined);
    });
  }, [approvals, api]);

  useEffect(() => {
    if (openAppAt) void showMainWindow();
  }, [openAppAt]);
  return null;
}

export default function App() {
  const route = useUi((s) => s.route);
  const go = useUi((s) => s.go);
  const connect = useRuntime((s) => s.connect);
  const conn = useRuntime((s) => s.conn);
  const approvals = useRuntime((s) => s.approvals);
  const tasks = useRuntime((s) => s);
  const settings = useSettings();
  const theme = settings.value<string>("theme", "system");
  const onboardingStep = settings.value("app_onboarding_step", ONBOARDING_DONE);

  useEffect(() => {
    void connect();
    const unsubs: Promise<() => void>[] = [
      onShellEvent("runtime-restarted", () => void connect()),
      onShellEvent<string>("navigate", (r) => go(r as Route)),
      onShellEvent("open-status", () => useUi.getState().setStatusOpen(true)),
      onShellEvent("runtime-crashed", () => void connect()),
    ];
    return () => unsubs.forEach((u) => void u.then((f) => f()));
  }, [connect, go]);

  useEffect(() => {
    if (conn === "online") void settings.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    else delete root.dataset.theme;
  }, [theme]);

  const running = runningTaskIds(tasks).length;
  const pending = Object.keys(approvals).length;
  const showOnboarding = conn === "online" && !settings.loading && onboardingStep < ONBOARDING_DONE;

  return (
    <TooltipPrimitive.Provider>
      <div className="app">
        <nav className="sidebar" aria-label="Main">
          <div className="brand">
            <img src="/scar-mark.svg" alt="" />
            <span>SCAR</span>
          </div>
          {NAV.map((n) => (
            <button key={n.id} className={clsx("nav-item", route === n.id && "active")} aria-current={route === n.id ? "page" : undefined} onClick={() => go(n.id)}>
              {n.icon}
              <span>{n.label}</span>
              {n.id === "conversation" && pending > 0 && <span className="nav-badge warn" aria-label={`${pending} waiting for approval`}>{pending}</span>}
              {n.id === "tasks" && running > 0 && <span className="nav-badge" aria-label={`${running} running`}>{running}</span>}
            </button>
          ))}
        </nav>
        <main className="main">
          <StatusBar />
          <ConnectionBanner />
          <div className="content">
            {route === "conversation" && <Conversation />}
            {route === "tasks" && <Tasks />}
            {route === "permissions" && <Permissions />}
            {route === "memory" && <MemoryScreen />}
            {route === "settings" && <Settings />}
            {route === "diagnostics" && <Diagnostics />}
          </div>
        </main>
        {showOnboarding && <Onboarding onDone={() => void settings.reload()} />}
        <ShellSync />
      </div>
    </TooltipPrimitive.Provider>
  );
}
