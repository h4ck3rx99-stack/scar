// Where you type (or talk). Enter sends, Shift+Enter adds a line. The microphone state is always visible while on.
import clsx from "clsx";
import { ArrowUp, Mic, MicOff, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useRuntime } from "../store/runtime";
import { IconButton, Kbd } from "./ui";

export function MicMeter({ level }: { level: number }) {
  const bars = 5;
  return (
    <span className="mic-meter" aria-hidden>
      {Array.from({ length: bars }, (_, i) => (
        <span key={i} className={clsx("mic-bar", level > (i + 0.5) / bars && "on")} />
      ))}
    </span>
  );
}

export function Composer({ autoFocus, placeholder, onSent, seed }: { autoFocus?: boolean; placeholder?: string; onSent?: (taskId: string) => void; seed?: { text: string; n: number } | null }) {
  const submit = useRuntime((s) => s.submit);
  const conn = useRuntime((s) => s.conn);
  const voice = useRuntime((s) => s.voice);
  const voiceCommand = useRuntime((s) => s.voiceCommand);
  const subscribe = useRuntime((s) => s.subscribe);
  const level = useRuntime((s) => s.micLevel);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const voiceOn = voice.state !== "off";

  useEffect(() => {
    if (seed) {
      setText(seed.text);
      ref.current?.focus();
    }
  }, [seed]);

  useEffect(() => {
    if (!voiceOn) return;
    subscribe("mic_level", true);
    return () => subscribe("mic_level", false);
  }, [voiceOn, subscribe]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  async function send() {
    const objective = text.trim();
    if (!objective || sending) return;
    setSending(true);
    const id = await submit(objective);
    setSending(false);
    if (id) {
      setText("");
      onSent?.(id);
    }
  }

  async function talk() {
    setVoiceError("");
    if (!voiceOn) {
      const err = await voiceCommand("start", "ptt");
      if (err) {
        setVoiceError(err);
        return;
      }
    }
    const err = await voiceCommand("push_to_talk");
    if (err) setVoiceError(err);
  }

  const listening = voice.state === "listening";
  return (
    <div className="composer-wrap">
      {voiceOn && (
        <div className={clsx("voice-strip", voice.muted && "muted")} role="status" aria-live="polite">
          {voice.muted ? <MicOff size={14} aria-hidden /> : <Mic size={14} aria-hidden />}
          <span className="voice-strip-label">
            {voice.muted ? "Microphone muted" : listening ? "Listening…" : voice.state === "thinking" ? "Thinking…" : voice.state === "speaking" ? "Speaking" : "Microphone on"}
          </span>
          {!voice.muted && listening && <MicMeter level={level} />}
          {voice.transcript && <span className="voice-transcript">“{voice.transcript}”</span>}
          <span className="spacer" />
          {voice.state === "speaking" && (
            <button className="link-btn" onClick={() => void voiceCommand("stop_speaking")}>
              <Volume2 size={13} aria-hidden /> Stop speaking
            </button>
          )}
          <button className="link-btn" onClick={() => void voiceCommand(voice.muted ? "unmute" : "mute")}>
            {voice.muted ? "Unmute" : "Mute microphone"}
          </button>
          <button className="link-btn" onClick={() => void voiceCommand("stop")}>
            Turn voice off
          </button>
        </div>
      )}
      <div className="composer">
        <textarea
          ref={ref}
          value={text}
          rows={1}
          autoFocus={autoFocus}
          aria-label="Ask SCAR"
          placeholder={placeholder ?? (conn === "online" ? "Ask SCAR to do something…" : "Waiting for SCAR to start…")}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <IconButton label={voiceOn ? "Talk (push to talk)" : "Turn on voice and talk"} className={clsx("composer-mic", listening && "active")} onClick={() => void talk()} disabled={conn !== "online" || voice.muted}>
          <Mic size={17} aria-hidden />
        </IconButton>
        <IconButton label="Send (Enter)" className="composer-send" onClick={() => void send()} disabled={!text.trim() || conn !== "online" || sending}>
          <ArrowUp size={17} aria-hidden />
        </IconButton>
      </div>
      <p className="composer-hint">
        {voiceError ? (
          <span className="text-danger">{voiceError}</span>
        ) : (
          <>
            <Kbd>Enter</Kbd> send · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> new line
          </>
        )}
      </p>
    </div>
  );
}
