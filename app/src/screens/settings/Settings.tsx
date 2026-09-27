import * as Tabs from "@radix-ui/react-tabs";
import { useUi } from "../../store/ui";
import { Accounts } from "./Accounts";
import { AiKeys } from "./AiKeys";
import { AdvancedSettings, AppSettings } from "./AppSettings";
import { VoiceSettings } from "./VoiceSettings";

const TABS = [
  { id: "ai", label: "AI & keys", el: <AiKeys /> },
  { id: "accounts", label: "Accounts", el: <Accounts /> },
  { id: "voice", label: "Voice", el: <VoiceSettings /> },
  { id: "app", label: "App", el: <AppSettings /> },
  { id: "advanced", label: "Advanced", el: <AdvancedSettings /> },
];

export function Settings() {
  const tab = useUi((s) => s.settingsTab);
  const go = useUi((s) => s.go);
  return (
    <div className="page">
      <div className="page-head">
        <h1>Settings</h1>
      </div>
      <Tabs.Root value={tab} onValueChange={(v) => go("settings", v)}>
        <Tabs.List className="tabs" aria-label="Settings sections">
          {TABS.map((t) => (
            <Tabs.Trigger key={t.id} value={t.id} className="tab">
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {TABS.map((t) => (
          <Tabs.Content key={t.id} value={t.id} className="tab-panel">
            {tab === t.id && t.el}
          </Tabs.Content>
        ))}
      </Tabs.Root>
    </div>
  );
}
