// Visual review: every screen and key state, light and dark, at 100% and 150% scaling.
// Opt-in (slow): SCAR_SCREENSHOTS=1 pnpm exec playwright test screenshots
import { join, resolve } from "node:path";
import { mkdirSync, writeFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const OUT = resolve(import.meta.dirname, "..", "..", "docs", "screenshots");
test.skip(!process.env.SCAR_SCREENSHOTS, "set SCAR_SCREENSHOTS=1 to capture screenshots");

const VARIANTS = [
  { scheme: "light" as const, scale: 1 },
  { scheme: "dark" as const, scale: 1 },
  { scheme: "light" as const, scale: 1.5 },
  { scheme: "dark" as const, scale: 1.5 },
];

async function shot(page: Page, name: string, scheme: string, scale: number) {
  mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(OUT, `${name}-${scheme}${scale === 1 ? "" : "@1.5x"}.png`) });
}

for (const v of VARIANTS) {
  test.describe(`${v.scheme} ${v.scale}x`, () => {
    test.use({ colorScheme: v.scheme, deviceScaleFactor: v.scale, viewport: { width: 1200, height: 780 } });

    test(`screens ${v.scheme} ${v.scale}`, async ({ app, rt }) => {
      await shot(app, "01-conversation-empty", v.scheme, v.scale);
      await app.getByRole("textbox", { name: "Ask SCAR" }).fill("In one sentence, what is RAM?");
      await app.keyboard.press("Enter");
      await expect(app.getByText("Answered").last()).toBeVisible();
      const src = join(rt.work, `notes-${v.scheme}-${v.scale}.txt`);
      writeFileSync(src, "notes\n");
      await app.getByRole("textbox", { name: "Ask SCAR" }).fill(`move ${src} to ${join(rt.work, `moved-${v.scheme}-${v.scale}.txt`)}`);
      await app.keyboard.press("Enter");
      await expect(app.getByRole("article", { name: /Approval needed/ })).toBeVisible();
      await shot(app, "02-conversation-approval", v.scheme, v.scale);
      await app.getByRole("article", { name: /Approval needed/ }).getByRole("button", { name: "Allow once" }).click();
      await expect(app.getByText("Done · verified").last()).toBeVisible();
      await shot(app, "03-conversation-done", v.scheme, v.scale);
      await app.getByRole("button", { name: "Open status", exact: true }).click();
      await expect(app.getByRole("dialog")).toBeVisible();
      await app.waitForTimeout(2500); // first resource snapshot
      await shot(app, "04-status-panel", v.scheme, v.scale);
      await app.keyboard.press("Escape");
      for (const [nav, file] of [["Tasks & Monitors", "05-tasks"], ["Permissions", "06-permissions"], ["Memory", "07-memory"]] as const) {
        await app.getByRole("button", { name: nav }).click();
        await app.waitForTimeout(600);
        await shot(app, file, v.scheme, v.scale);
      }
      await app.getByRole("button", { name: "Settings" }).click();
      for (const [tab, file] of [["AI & keys", "08-settings-ai"], ["Accounts", "09-settings-accounts"], ["Voice", "10-settings-voice"], ["App", "11-settings-app"]] as const) {
        await app.getByRole("tab", { name: tab }).click();
        await app.waitForTimeout(700);
        await shot(app, file, v.scheme, v.scale);
      }
      await app.getByRole("button", { name: "Diagnostics" }).click();
      await expect(app.locator(".check").first()).toBeVisible({ timeout: 45_000 });
      await shot(app, "12-diagnostics", v.scheme, v.scale);
      await rt.api("PATCH", "/settings", { values: { app_onboarding_step: 2 } });
      await app.reload();
      await expect(app.getByRole("heading", { name: "Connect a free AI" })).toBeVisible();
      await shot(app, "13-onboarding", v.scheme, v.scale);
      await rt.api("PATCH", "/settings", { values: { app_onboarding_step: 99 } });
    });

    test(`quick bar and indicator ${v.scheme} ${v.scale}`, async ({ page, rt }) => {
      await page.addInitScript((ep) => {
        (window as unknown as { __SCAR_ENDPOINT__: unknown }).__SCAR_ENDPOINT__ = ep;
      }, { url: rt.url, token: rt.token });
      await page.setViewportSize({ width: 700, height: 360 });
      await page.goto("/quickbar.html");
      await page.getByRole("textbox", { name: "Ask SCAR" }).fill("In one sentence, what is RAM?");
      await page.keyboard.press("Enter");
      await expect(page.getByText("Answered")).toBeVisible();
      await shot(page, "14-quickbar", v.scheme, v.scale);
      await page.setViewportSize({ width: 760, height: 60 });
      await page.goto("/indicator.html");
      await page.waitForTimeout(500);
      await shot(page, "15-indicator", v.scheme, v.scale);
    });
  });
}
