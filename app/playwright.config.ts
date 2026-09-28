import { defineConfig } from "@playwright/test";

// End-to-end tests drive the built frontend (vite preview) in headless Edge against a real, sandboxed SCAR runtime
// with a mock AI provider (scripts/e2e_runtime.py). Nothing touches the user's data, keys or desktop.
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://localhost:1420",
    channel: "msedge",
    headless: true,
    viewport: { width: 1280, height: 820 },
  },
  webServer: {
    command: "pnpm preview",
    port: 1420,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
