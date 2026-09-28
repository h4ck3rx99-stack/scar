// Start a sandboxed runtime with a mock AI provider and wait until it serves the app API.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export const E2E_ROOT = join(tmpdir(), "scar-e2e");

export default async function globalSetup(): Promise<void> {
  rmSync(E2E_ROOT, { recursive: true, force: true });
  mkdirSync(E2E_ROOT, { recursive: true });
  const repo = resolve(import.meta.dirname, "..", "..");
  const child = spawn("uv", ["run", "--project", repo, "python", join(repo, "scripts", "e2e_runtime.py"), "--root", E2E_ROOT], {
    cwd: repo,
    stdio: "ignore",
    detached: false,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  writeFileSync(join(E2E_ROOT, "runtime.pid"), String(child.pid));
  const ready = join(E2E_ROOT, "ready.json");
  for (let i = 0; i < 240; i++) {
    if (existsSync(ready)) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("the e2e runtime did not start (see scripts/e2e_runtime.py)");
}
