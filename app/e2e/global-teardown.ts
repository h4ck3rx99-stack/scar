import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { E2E_ROOT } from "./global-setup";

export default async function globalTeardown(): Promise<void> {
  const ready = join(E2E_ROOT, "ready.json");
  if (!existsSync(ready)) return;
  const ep = JSON.parse(readFileSync(ready, "utf-8")) as { url: string; token: string };
  try {
    await fetch(`${ep.url}/api/v1/shutdown`, { method: "POST", headers: { Authorization: `Bearer ${ep.token}` } });
  } catch {
    /* already stopped */
  }
}
