// Always-visible status: what SCAR is doing, the AI connection in plain words, the microphone, and Stop All.
import * as Dialog from "@radix-ui/react-dialog";
import clsx from "clsx";
import { Activity, Cpu, Gamepad2, Mic, MicOff, OctagonX, X } from "lucide-react";
import { useEffect } from "react";
import { useApiGet } from "../hooks/useApi";
import { useRuntime } from "../store/runtime";
import { useUi } from "../store/ui";
import { Badge, Button, Tip } from "./ui";

const STATE_WORDS: Record<string, string> = {
  ready: "Ready",
  working: "Working",
  listening: "Listening",
  waiting: "Waiting for you",
  speaking: "Speaking",
  offline: "Offline",
};

export function prettyKey(k: string): string {
  return k
    .replace(/[<>]/g, "")
    .split("+")
    .map((p) => (p.length === 1 ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)))
    .join("+");
}

export function StatePill() {
  const state = useRuntime((s) => s.assistant);
  const conn = useRuntime((s) => s.conn);
  const shown = conn === "online" ? state : "offline";
  return (
    <span className={clsx("state-pill", `state-${shown}`)} role="status" aria-live="polite">
      <span className="state-dot" aria-hidden />
      {STATE_WORDS[shown] ?? shown}
    </span>
  );
}

export function StatusBar() {
  const provider = useRuntime((s) => s.provider);
  const voice = useRuntime((s) => s.voice);
  const voiceCommand = useRuntime((s) => s.voiceCommand);
  const game = useRuntime((s) => s.gameMode);
  const stopAll = useRuntime((s) => s.stopAll);
  const killKey = useRuntime((s) => s.killKey);
  const conn = useRuntime((s) => s.conn);
  const setOpen = useUi((s) => s.setStatusOpen);
  const micOn = voice.state !== "off" && voice.mic_active;

  return (
    <header className="statusbar">
      <StatePill />
      <button className={clsx("ai-chip", `ai-${provider.level}`)} onClick={() => setOpen(true)} aria-label={`AI connection: ${provider.summary}. Open status`}>
        {provider.summary}
      </button>
      {game && (
        <Tip label="A full-screen app is running: notifications and speech wait, and background tasks won't take over the screen">
          <span className="chip chip-muted">
            <Gamepad2 size={14} aria-hidden /> Game mode
          </span>
        </Tip>
      )}
      <span className="spacer" />
      {voice.state !== "off" && (
        <Tip label={voice.muted ? "Microphone muted: click to unmute" : "Microphone is on: click to mute"}>
          <button className={clsx("mic-indicator", micOn && "live", voice.muted && "muted")} onClick={() => void voiceCommand(voice.muted ? "unmute" : "mute")} aria-label={voice.muted ? "Unmute microphone" : "Mute microphone"}>
            {voice.muted ? <MicOff size={15} aria-hidden /> : <Mic size={15} aria-hidden />}
            <span>{voice.muted ? "Mic muted" : "Mic on"}</span>
          </button>
        </Tip>
      )}
      <Tip label="Status and resources">
        <button className="icon-btn" aria-label="Open status" onClick={() => setOpen(true)}>
          <Activity size={17} />
        </button>
      </Tip>
      <Tip label={`Stop everything SCAR is doing${killKey ? ` (${prettyKey(killKey)})` : ""}`}>
        <Button variant="danger" size="sm" icon={<OctagonX size={15} aria-hidden />} onClick={() => void stopAll()} disabled={conn !== "online"}>
          Stop All
        </Button>
      </Tip>
      <StatusPanel />
    </header>
  );
}

function Meter({ label, value, max, unit }: { label: string; value: number; max: number; unit: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="muted">
          {Math.round(value).toLocaleString()} / {Math.round(max).toLocaleString()} {unit}
        </span>
      </div>
      <div className="meter-track" role="progressbar" aria-label={label} aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={clsx("meter-fill", pct > 85 && "hot")} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function StatusPanel() {
  const open = useUi((s) => s.statusOpen);
  const setOpen = useUi((s) => s.setStatusOpen);
  const subscribe = useRuntime((s) => s.subscribe);
  const res = useRuntime((s) => s.resources) as null | {
    cpu_percent: number; ram_used_mb: number; ram_total_mb: number; scar_rss_mb: number;
    gpu: null | { name: string; util_percent: number; vram_used_mb: number; vram_total_mb: number };
    battery_percent: number | null; on_ac: boolean | null; local_models: { name: string; idle_s: number | null }[];
  };
  const provider = useRuntime((s) => s.provider);
  const voice = useRuntime((s) => s.voice);
  const game = useRuntime((s) => s.gameMode);
  const state = useRuntime((s) => s.assistant);
  const hello = useApiGet<{ pid: number; app_version: string; data_dir: string }>(open ? "/hello" : null, [open]);

  useEffect(() => {
    if (!open) return;
    subscribe("resources", true);
    return () => subscribe("resources", false);
  }, [open, subscribe]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="drawer" aria-describedby={undefined}>
          <div className="drawer-head">
            <Dialog.Title>Status</Dialog.Title>
            <Dialog.Close asChild>
              <button className="icon-btn" aria-label="Close">
                <X size={17} />
              </button>
            </Dialog.Close>
          </div>
          <section className="status-section">
            <h3>SCAR</h3>
            <p>
              <StatePill /> <span className="muted small">{hello.data ? `Runtime PID ${hello.data.pid} · v${hello.data.app_version}` : ""}</span>
            </p>
            {game && <p className="muted small">Game mode: a full-screen app is in front, so notifications and speech are held.</p>}
            {state === "offline" && <p className="text-danger small">The runtime isn't reachable.</p>}
          </section>
          <section className="status-section">
            <h3>AI connection</h3>
            <p>
              <Badge tone={provider.level === "cloud" ? "ok" : provider.level === "local" ? "info" : "warn"}>
                {provider.level === "cloud" ? "Cloud" : provider.level === "local" ? "Local" : "Limited"}
              </Badge>{" "}
              {provider.summary}
            </p>
            <p className="muted small">
              Local model: {res?.local_models.some((m) => /llama|ollama/i.test(m.name)) ? res.local_models.filter((m) => /llama|ollama/i.test(m.name)).map((m) => m.name).join(", ") + " loaded" : "not loaded (loads on demand, unloads when idle)"}
            </p>
          </section>
          <section className="status-section">
            <h3>This computer</h3>
            {!res ? (
              <div className="skeleton">
                <div className="skeleton-line" />
                <div className="skeleton-line" />
              </div>
            ) : (
              <>
                <Meter label={`CPU ${res.cpu_percent.toFixed(0)}%`} value={res.cpu_percent} max={100} unit="%" />
                <Meter label="Memory" value={res.ram_used_mb} max={res.ram_total_mb} unit="MB" />
                {res.gpu && <Meter label={`GPU ${res.gpu.util_percent.toFixed(0)}% · ${res.gpu.name}`} value={res.gpu.vram_used_mb} max={res.gpu.vram_total_mb} unit="MB VRAM" />}
                <p className="muted small">
                  <Cpu size={12} aria-hidden /> SCAR uses {res.scar_rss_mb.toFixed(0)} MB
                  {res.battery_percent != null && ` · battery ${res.battery_percent.toFixed(0)}%${res.on_ac ? " (plugged in)" : ""}`}
                </p>
              </>
            )}
          </section>
          <section className="status-section">
            <h3>Voice</h3>
            <p>
              {voice.state === "off" ? "Off" : voice.muted ? "On · microphone muted" : `On · ${voice.state}`}
              {voice.mode && voice.state !== "off" && <span className="muted small"> · {voice.mode === "wake" ? "wake word" : "push to talk"}</span>}
            </p>
            {voice.degraded && <p className="text-danger small">{voice.degraded}</p>}
          </section>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
