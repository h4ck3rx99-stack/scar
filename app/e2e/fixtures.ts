import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { E2E_ROOT } from "./global-setup";

export interface Runtime {
  url: string;
  token: string;
  work: string;
  api: <T = unknown>(method: string, path: string, body?: unknown) => Promise<T>;
}

function runtime(): Runtime {
  const ep = JSON.parse(readFileSync(join(E2E_ROOT, "ready.json"), "utf-8")) as { url: string; token: string; work: string };
  return {
    ...ep,
    async api<T>(method: string, path: string, body?: unknown) {
      const r = await fetch(`${ep.url}/api/v1${path}`, {
        method,
        headers: { Authorization: `Bearer ${ep.token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return (await r.json()) as T;
    },
  };
}

export const test = base.extend<{ rt: Runtime; app: Page }>({
  rt: async ({}, use) => {
    await use(runtime());
  },
  app: async ({ page, rt }, use) => {
    await page.addInitScript((ep) => {
      (window as unknown as { __SCAR_ENDPOINT__: unknown }).__SCAR_ENDPOINT__ = ep;
    }, { url: rt.url, token: rt.token });
    await page.goto("/");
    await expect(page.getByRole("status").filter({ hasText: /Ready|Working|Waiting/ }).first()).toBeVisible();
    await use(page);
  },
});

/** No serious or critical accessibility violations on the current screen. */
export async function expectAccessible(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")})`)).toEqual([]);
}

export { expect };
