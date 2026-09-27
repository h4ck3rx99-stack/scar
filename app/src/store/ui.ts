// Window-local UI state: which screen is open and per-viewer conveniences (never runtime state).
import { create } from "zustand";

export type Route = "conversation" | "tasks" | "permissions" | "memory" | "settings" | "diagnostics";

const LS = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage may be unavailable; these are conveniences only */
    }
  },
};

interface Ui {
  route: Route;
  statusOpen: boolean;
  settingsTab: string;
  trayHintSeen: boolean;
  go: (r: Route, tab?: string) => void;
  setStatusOpen: (open: boolean) => void;
  markTrayHintSeen: () => void;
}

export const useUi = create<Ui>((set) => ({
  route: (LS.get("scar.route") as Route | null) ?? "conversation",
  statusOpen: false,
  settingsTab: "ai",
  trayHintSeen: LS.get("scar.trayHintSeen") === "1",
  go(route, tab) {
    LS.set("scar.route", route);
    set({ route, ...(tab ? { settingsTab: tab } : {}) });
  },
  setStatusOpen(statusOpen) {
    set({ statusOpen });
  },
  markTrayHintSeen() {
    LS.set("scar.trayHintSeen", "1");
    set({ trayHintSeen: true });
  },
}));
