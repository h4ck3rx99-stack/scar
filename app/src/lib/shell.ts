// Bridge to the Tauri shell. Everything here degrades gracefully in a plain browser (tests, dev), where the
// runtime endpoint is injected as window.__SCAR_ENDPOINT__ by the test harness.
import type { Endpoint } from "../api/client";

declare global {
  interface Window {
    __SCAR_ENDPOINT__?: Endpoint;
    __TAURI_INTERNALS__?: unknown;
  }
}

export const isTauri = (): boolean => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const core = await import("@tauri-apps/api/core");
  return core.invoke<T>(cmd, args);
}

export interface RuntimeInfo {
  endpoint: Endpoint | null;
  state: "starting" | "installing" | "ready" | "crashed" | "missing";
  message: string;
}

/** Ask the shell for the runtime (it attaches to a running daemon or starts one as a managed sidecar). */
export async function runtimeInfo(): Promise<RuntimeInfo> {
  if (isTauri()) return invoke<RuntimeInfo>("runtime_info");
  let ep = window.__SCAR_ENDPOINT__ ?? null;
  if (!ep && import.meta.env.DEV) {
    try {
      const r = await fetch("/__scar_endpoint");
      const j = r.ok ? ((await r.json()) as Partial<Endpoint>) : {};
      if (j.url && j.token) ep = { url: j.url, token: j.token };
    } catch {
      ep = null;
    }
  }
  return { endpoint: ep, state: ep ? "ready" : "missing", message: ep ? "" : "No runtime endpoint (test/dev mode)." };
}

export async function restartRuntime(): Promise<void> {
  if (isTauri()) await invoke("restart_runtime");
}

/** Open an http(s) URL in the user's default browser. Anything else is refused. */
export async function openExternal(url: string): Promise<boolean> {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return false;
  if (isTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(u.toString());
  } else {
    window.open(u.toString(), "_blank", "noopener,noreferrer");
  }
  return true;
}

/** Reveal a local file in Explorer (diagnostic bundle, saved documents). */
export async function revealPath(path: string): Promise<void> {
  if (isTauri()) {
    const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
    await revealItemInDir(path);
  }
}

export type TrayState = "idle" | "working" | "listening" | "approval" | "error";

export async function setTrayState(state: TrayState, tooltip: string): Promise<void> {
  if (isTauri()) await invoke("set_tray_state", { state, tooltip });
}

export async function setIndicator(active: boolean, text: string, killKey: string): Promise<void> {
  if (isTauri()) await invoke("set_indicator", { active, text, killKey });
}

export async function showMainWindow(route?: string): Promise<void> {
  if (isTauri()) await invoke("show_main", { route: route ?? null });
}

export async function hideQuickBar(): Promise<void> {
  if (isTauri()) await invoke("hide_quickbar");
}

export async function mainWindowVisible(): Promise<boolean> {
  if (!isTauri()) return document.visibilityState === "visible";
  return invoke<boolean>("main_visible");
}

export async function quitApp(): Promise<void> {
  if (isTauri()) await invoke("quit_app");
}

/** Register the Quick Bar shortcut; returns an error message when Windows refuses it (another app owns it). */
export async function registerQuickBarShortcut(accelerator: string): Promise<string | null> {
  if (!isTauri()) return null;
  try {
    await invoke("register_quickbar_shortcut", { accelerator });
    return null;
  } catch (e) {
    return String(e);
  }
}

export async function setAutostart(enabled: boolean): Promise<boolean> {
  if (!isTauri()) return false;
  const a = await import("@tauri-apps/plugin-autostart");
  if (enabled) await a.enable();
  else await a.disable();
  return a.isEnabled();
}

export async function autostartEnabled(): Promise<boolean> {
  if (!isTauri()) return false;
  const a = await import("@tauri-apps/plugin-autostart");
  return a.isEnabled();
}

export async function appVersion(): Promise<string> {
  if (!isTauri()) return "dev";
  const { getVersion } = await import("@tauri-apps/api/app");
  return getVersion();
}

export async function onShellEvent<T>(name: string, handler: (payload: T) => void): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(name, (e) => handler(e.payload));
}

export async function resizeQuickBar(height: number): Promise<void> {
  if (isTauri()) await invoke("resize_quickbar", { height });
}
