import { Mic } from "lucide-react";
import { useState } from "react";
import { humanError } from "../../api/client";
import { Button, Card, ErrorState, Field, Skeleton, Switch } from "../../components/ui";
import { useApiGet, useSettings } from "../../hooks/useApi";
import { useRuntime } from "../../store/runtime";

interface Devices { microphones: { name: string; default: boolean }[]; speakers: { name: string; default: boolean }[] }
interface TestResult { ok: boolean; message?: string; heard?: string; level?: number; stt?: string; tts?: string; hint?: string; tts_error?: string }

const WAKE_WORDS = [
  { id: "hey_jarvis", label: "“Hey Jarvis”" },
  { id: "hey_mycroft", label: "“Hey Mycroft”" },
  { id: "alexa", label: "“Alexa”" },
  { id: "hey_rhasspy", label: "“Hey Rhasspy”" },
];

export function VoiceSettings() {
  const s = useSettings();
  const devices = useApiGet<Devices>("/voice/devices");
  const api = useRuntime((st) => st.api);
  const voice = useRuntime((st) => st.voice);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [err, setErr] = useState("");

  async function save(values: Record<string, unknown>) {
    setErr((await s.save(values)) ?? "");
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api!.post<TestResult>("/voice/test", { seconds: 4, speak: true }));
    } catch (e) {
      setTest({ ok: false, message: humanError(e) });
    } finally {
      setTesting(false);
    }
  }

  if (s.loading) return <Skeleton lines={6} />;
  return (
    <>
      {err && <ErrorState message={err} />}
      <Card title="Microphone and speakers">
        {devices.loading ? (
          <Skeleton />
        ) : devices.error ? (
          <ErrorState message={devices.error} />
        ) : (
          <div className="form-grid">
            <Field label="Microphone" htmlFor="mic-device">
              <select id="mic-device" value={s.value("mic_device", "")} onChange={(e) => void save({ mic_device: e.target.value })}>
                <option value="">Windows default{devices.data?.microphones.find((m) => m.default) ? ` (${devices.data.microphones.find((m) => m.default)!.name})` : ""}</option>
                {devices.data?.microphones.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Speakers" htmlFor="spk-device">
              <select id="spk-device" value={s.value("speaker_device", "")} onChange={(e) => void save({ speaker_device: e.target.value })}>
                <option value="">Windows default{devices.data?.speakers.find((m) => m.default) ? ` (${devices.data.speakers.find((m) => m.default)!.name})` : ""}</option>
                {devices.data?.speakers.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}
        <div className="test-row">
          <Button icon={<Mic size={15} aria-hidden />} busy={testing} disabled={voice.state !== "off"} onClick={() => void runTest()}>
            {testing ? "Recording 4 seconds… say something" : "Test microphone"}
          </Button>
          {voice.state !== "off" && <span className="muted small">Turn voice off to run the test.</span>}
        </div>
        {test && (
          <div className={test.ok ? "test-result ok" : "test-result bad"} role="status">
            {test.ok ? (
              <>
                <p>
                  Heard: <strong>{test.heard ? `“${test.heard}”` : "nothing I could understand"}</strong>
                </p>
                <p className="muted small">
                  Level {test.level?.toFixed(3)} · transcribed by {test.stt === "faster-whisper" ? "local speech recognition" : "cloud speech recognition"}
                  {test.tts ? " · played back" : ""}
                </p>
                {test.hint && <p className="text-warn small">{test.hint}</p>}
                {test.tts_error && <p className="text-warn small">Couldn't play the reply: {test.tts_error}</p>}
              </>
            ) : (
              <p>{test.message}</p>
            )}
          </div>
        )}
      </Card>
      <Card title="Talking to SCAR">
        <div className="setting-row">
          <div>
            <p className="setting-title">Wake word</p>
            <p className="muted small">Listen for a phrase instead of pressing the talk button. The microphone stays on while voice is on (the mic indicator shows it); audio is processed on this computer.</p>
          </div>
          <Switch label="Wake word" checked={s.value("voice_enabled", false)} onChange={(v) => void save({ voice_enabled: v })} />
        </div>
        <div className="form-grid">
          <Field label="Wake phrase" htmlFor="wake-word">
            <select id="wake-word" value={s.value("wake_word", "hey_jarvis")} onChange={(e) => void save({ wake_word: e.target.value })}>
              {WAKE_WORDS.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Push-to-talk key" htmlFor="ptt" hint="Held anywhere in Windows while voice is on.">
            <input id="ptt" defaultValue={s.value("ptt_hotkey", "")} onBlur={(e) => e.target.value !== s.value("ptt_hotkey", "") && void save({ ptt_hotkey: e.target.value })} />
          </Field>
          <Field label="SCAR's voice" htmlFor="tts-voice" hint="For example en-US-AriaNeural, or part of a Windows voice name.">
            <input id="tts-voice" defaultValue={s.value("tts_voice", "")} onBlur={(e) => e.target.value !== s.value("tts_voice", "") && void save({ tts_voice: e.target.value })} />
          </Field>
          <Field label="Speech output" htmlFor="tts-provider">
            <select id="tts-provider" value={s.value("tts_provider", "auto")} onChange={(e) => void save({ tts_provider: e.target.value })}>
              <option value="auto">Automatic (online voice, falls back to Windows voice)</option>
              <option value="edge-tts">Online voice (edge-tts)</option>
              <option value="sapi">Windows voice (offline)</option>
              <option value="azure-speech">Azure Speech (needs a key)</option>
              <option value="none">Don't speak</option>
            </select>
          </Field>
        </div>
      </Card>
    </>
  );
}
