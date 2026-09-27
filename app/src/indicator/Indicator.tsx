// The always-on-top pill shown while SCAR controls the mouse, keyboard or windows (and briefly when it captures
// the screen). The shell shows/hides this window; the Stop button triggers the same kill switch as the hotkey.
import { Eye, OctagonX } from "lucide-react";
import { useEffect, useState } from "react";
import { prettyKey } from "../components/StatusBar";
import { onShellEvent } from "../lib/shell";
import { useRuntime } from "../store/runtime";

interface Payload {
  active: boolean;
  text: string;
  killKey: string;
}

export function Indicator() {
  const connect = useRuntime((s) => s.connect);
  const stopAll = useRuntime((s) => s.stopAll);
  const captureAt = useRuntime((s) => s.captureAt);
  const [p, setP] = useState<Payload>({ active: false, text: "", killKey: "" });
  const [capturing, setCapturing] = useState(false);

  useEffect(() => {
    void connect();
    const un = onShellEvent<Payload>("indicator", setP);
    return () => void un.then((f) => f());
  }, [connect]);

  useEffect(() => {
    if (!captureAt) return;
    setCapturing(true);
    const t = setTimeout(() => setCapturing(false), 1500);
    return () => clearTimeout(t);
  }, [captureAt]);

  return (
    <div className="indicator" role="alert" aria-live="assertive">
      <span className="indicator-dot" aria-hidden />
      {capturing && !p.active ? (
        <span>
          <Eye size={14} aria-hidden /> SCAR is looking at your screen
        </span>
      ) : (
        <span>
          SCAR is controlling your computer{p.text ? ` · ${p.text}` : ""}
          {p.killKey && <span className="indicator-key"> · {prettyKey(p.killKey)} stops it</span>}
        </span>
      )}
      <button className="indicator-stop" onClick={() => void stopAll()}>
        <OctagonX size={14} aria-hidden /> Stop
      </button>
    </div>
  );
}
