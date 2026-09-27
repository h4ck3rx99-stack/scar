// Spotlight-style entry point: type or talk, Enter, watch progress inline, Esc to dismiss.
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { ArrowUpRight, Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { OutcomeBadge } from "../components/TaskCard";
import { hideQuickBar, isTauri, onShellEvent, resizeQuickBar, showMainWindow } from "../lib/shell";
import { SafeMarkdown } from "../lib/SafeMarkdown";
import { useRuntime } from "../store/runtime";

export function QuickBar() {
  const connect = useRuntime((s) => s.connect);
  const submit = useRuntime((s) => s.submit);
  const conn = useRuntime((s) => s.conn);
  const tasks = useRuntime((s) => s.tasks);
  const approvals = useRuntime((s) => s.approvals);
  const voice = useRuntime((s) => s.voice);
  const voiceCommand = useRuntime((s) => s.voiceCommand);
  const lastError = useRuntime((s) => s.lastError);
  const [text, setText] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const task = taskId ? tasks[taskId] : undefined;
  const pending = Object.values(approvals).find((a) => !taskId || a.task_id === taskId);

  useEffect(() => {
    void connect();
    const un = onShellEvent("quickbar-shown", () => {
      input.current?.focus();
      input.current?.select();
    });
    return () => void un.then((f) => f());
  }, [connect]);

  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // the window hugs its content: a transparent oversized window would still swallow clicks
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => void resizeQuickBar(Math.ceil(el.getBoundingClientRect().height) + 16));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") void hideQuickBar();
    };
    const onBlur = () => {
      // stay open while something needs the user; otherwise behave like Spotlight
      if (!pending && (!task || task.outcome.kind !== "running")) void hideQuickBar();
    };
    window.addEventListener("keydown", onKey);
    if (isTauri()) window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", onBlur);
    };
  }, [pending, task]);

  async function send() {
    const t = text.trim();
    if (!t) return;
    const id = await submit(t);
    if (id) {
      setTaskId(id);
      setText("");
    }
  }

  async function talk() {
    if (voice.state === "off") await voiceCommand("start", "ptt");
    await voiceCommand("push_to_talk");
  }

  return (
    <TooltipPrimitive.Provider>
      <div className="qb" ref={box}>
        <form
          className="qb-bar"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <img src="/scar-mark.svg" alt="" className="qb-mark" />
          <input
            ref={input}
            autoFocus
            aria-label="Ask SCAR"
            placeholder={conn === "online" ? "Ask SCAR to do something…" : "SCAR is starting…"}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="button" className={clsx("icon-btn", voice.state === "listening" && "active")} aria-label="Hold to talk" onClick={() => void talk()} disabled={conn !== "online" || voice.muted}>
            <Mic size={18} />
          </button>
        </form>
        {lastError && !task && <p className="qb-error">{lastError}</p>}
        {voice.state !== "off" && (voice.state === "listening" || voice.transcript) && (
          <p className="qb-voice">{voice.state === "listening" ? "Listening…" : `“${voice.transcript}”`}</p>
        )}
        {task && (
          <div className="qb-result" aria-live="polite">
            <ul className="qb-steps">
              {task.steps.filter((s) => s.tool !== "finish").slice(-4).map((s) => (
                <li key={s.actionId} className={clsx("step", `step-${s.status}`)}>
                  {s.title}
                </li>
              ))}
            </ul>
            {(task.draft || task.result) && <SafeMarkdown className="md qb-md" text={task.outcome.kind === "running" ? task.draft : task.result} />}
            <div className="qb-foot">
              <OutcomeBadge outcome={task.outcome} />
              <span className="spacer" />
              <button className="link-btn" onClick={() => void showMainWindow("conversation").then(() => hideQuickBar())}>
                Open in SCAR <ArrowUpRight size={13} aria-hidden />
              </button>
            </div>
          </div>
        )}
        {pending && <ApprovalCard approval={pending} compact />}
      </div>
    </TooltipPrimitive.Provider>
  );
}
