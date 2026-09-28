import { join } from "node:path";
import { existsSync } from "node:fs";
import { expect, expectAccessible, test } from "./fixtures";



test("onboarding: step through, resume and finish", async ({ page, rt }) => {
  await rt.api("PATCH", "/settings", { values: { app_onboarding_step: 0 } });
  await page.addInitScript((ep) => {
    (window as unknown as { __SCAR_ENDPOINT__: unknown }).__SCAR_ENDPOINT__ = ep;
  }, { url: rt.url, token: rt.token });
  await page.goto("/");
  const dlg = page.getByRole("dialog", { name: "Set up SCAR" });
  await expect(dlg.getByRole("heading", { name: "Welcome to SCAR" })).toBeVisible();
  await expectAccessible(page, "onboarding");
  await dlg.getByRole("button", { name: "Get started" }).click();
  await expect(dlg.getByRole("heading", { name: "Where may SCAR work?" })).toBeVisible();
  await dlg.getByRole("button", { name: "Next" }).click();
  await expect(dlg.getByRole("heading", { name: "Connect a free AI" })).toBeVisible();
  // resumable: reload lands on the same step
  await page.reload();
  await expect(page.getByRole("heading", { name: "Connect a free AI" })).toBeVisible();
  await page.getByRole("button", { name: "Skip setup" }).click();
  await expect(dlg).toHaveCount(0);
  const s = await rt.api<{ fields: { name: string; value: unknown }[] }>("GET", "/settings");
  expect(s.fields.find((f) => f.name === "app_onboarding_step")?.value).toBe(99);
});

test("a question streams an answer and is marked Answered", async ({ app }) => {
  await expectAccessible(app, "conversation empty");
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill("In one sentence, what is RAM?");
  await app.keyboard.press("Enter");
  await expect(app.getByText("RAM is your computer's short-term working memory", { exact: false })).toBeVisible();
  await expect(app.getByText("Answered")).toBeVisible();
  await expectAccessible(app, "conversation with answer");
});

test("an approval shows exactly what will happen; Allow once runs it and verifies", async ({ app, rt }) => {
  const src = join(rt.work, "report.txt");
  const dst = join(rt.work, "archive.txt");
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill(`move ${src} to ${dst}`);
  await app.keyboard.press("Enter");
  const card = app.getByRole("article", { name: /Approval needed/ });
  await expect(card).toBeVisible();
  await expect(card.getByText("High risk", { exact: true })).toBeVisible();
  await expect(card.getByText(/Why SCAR is asking/)).toBeVisible();
  await expectAccessible(app, "approval card");
  await card.getByRole("button", { name: "Allow once" }).click();
  await expect(card).toHaveCount(0);
  await expect(app.getByText("Done · verified")).toBeVisible();
  expect(existsSync(dst)).toBe(true);
});

test("Deny leaves things as they were and says the task failed", async ({ app, rt }) => {
  const src = join(rt.work, "archive.txt");
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill(`move ${src} to ${join(rt.work, "again.txt")}`);
  await app.keyboard.press("Enter");
  const card = app.getByRole("article", { name: /Approval needed/ });
  await card.getByRole("button", { name: "Deny" }).click();
  await expect(app.getByText("Failed").last()).toBeVisible();
  expect(existsSync(src)).toBe(true);
});

test("Cancel stops a running task", async ({ app }) => {
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill("what is a slow answer to everything");
  await app.keyboard.press("Enter");
  const cancel = app.getByRole("button", { name: "Cancel" }).last();
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(app.getByText("Cancelled").last()).toBeVisible();
});

test("Stop All cancels running work", async ({ app }) => {
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill("what is another slow answer");
  await app.keyboard.press("Enter");
  await expect(app.getByRole("button", { name: "Cancel" }).last()).toBeVisible();
  await app.getByRole("button", { name: "Stop All" }).click();
  await expect(app.getByText("Cancelled").last()).toBeVisible();
});

test("settings: saving a key and testing it with a real call (mock provider)", async ({ app }) => {
  await app.getByRole("button", { name: "Settings" }).click();
  await expect(app.getByRole("tab", { name: "AI & keys" })).toBeVisible();
  const groq = app.locator(".provider").filter({ hasText: "Groq" }).first();
  await groq.getByLabel("GROQ_API_KEY value").fill("gsk_mock_" + "y".repeat(40));
  await groq.getByRole("button", { name: "Save key" }).click();
  await expect(groq.getByText("Saved to Windows Credential Manager.")).toBeVisible();
  await expect(groq.getByLabel("GROQ_API_KEY value")).toHaveValue("");
  await groq.getByRole("button", { name: "Test with a real call" }).click();
  await expect(groq.getByText(/Works: mock-model answered/)).toBeVisible();
  await expectAccessible(app, "settings ai");
  for (const tab of ["Accounts", "Voice", "App", "Advanced"]) {
    await app.getByRole("tab", { name: tab }).click();
    await expect(app.getByRole("tabpanel", { name: tab })).toBeVisible();
    await expectAccessible(app, `settings ${tab}`);
  }
});

test("memory: edit and forget", async ({ app, rt }) => {
  await rt.api("POST", "/tasks", { objective: "remember that my test editor is VS Code" });
  await app.getByRole("button", { name: "Memory" }).click();
  const row = app.locator(".row").filter({ hasText: "my test editor is VS Code" });
  await expect(row).toBeVisible();
  await expectAccessible(app, "memory");
  await row.getByRole("button", { name: "Edit" }).click();
  await app.getByLabel("Memory text").fill("my test editor is Neovim");
  await app.getByRole("button", { name: "Save" }).click();
  const edited = app.locator(".row").filter({ hasText: "my test editor is Neovim" });
  await expect(edited).toBeVisible();
  await edited.getByRole("button", { name: "Forget" }).click();
  await expect(edited).toHaveCount(0);
});

test("permissions: a saved permission can be revoked", async ({ app, rt }) => {
  const src = join(rt.work, "archive.txt");
  await app.getByRole("textbox", { name: "Ask SCAR" }).fill(`move ${src} to ${join(rt.work, "archive2.txt")}`);
  await app.keyboard.press("Enter");
  const card = app.getByRole("article", { name: /Approval needed/ });
  await card.getByRole("button", { name: /Always/ }).click();
  await app.getByRole("menuitem", { name: /Always allow this exact action/ }).click();
  await expect(app.getByText("Done · verified").last()).toBeVisible();
  await app.getByRole("button", { name: "Permissions" }).click();
  const grant = app.locator(".card").filter({ has: app.getByRole("heading", { name: "Saved permissions" }) }).locator(".row").filter({ hasText: "fs.move" });
  await expect(grant).toBeVisible();
  await expectAccessible(app, "permissions");
  await grant.getByRole("button", { name: "Revoke" }).click();
  await expect(grant).toHaveCount(0);
});

test("tasks and diagnostics screens render real data", async ({ app }) => {
  await app.getByRole("button", { name: "Tasks & Monitors" }).click();
  await expect(app.getByRole("heading", { name: "Recent" })).toBeVisible();
  await expect(app.locator(".row").filter({ hasText: "what is RAM" }).first()).toBeVisible();
  await expectAccessible(app, "tasks");
  await app.getByRole("button", { name: "Diagnostics" }).click();
  await expect(app.getByRole("heading", { name: "Health check" })).toBeVisible();
  await expect(app.locator(".check").first()).toBeVisible({ timeout: 45_000 });
  await expectAccessible(app, "diagnostics");
});

test("keyboard only: navigate, type and send", async ({ app }) => {
  await app.getByRole("button", { name: "Conversation" }).focus();
  await app.keyboard.press("Enter");
  const box = app.getByRole("textbox", { name: "Ask SCAR" });
  await box.focus();
  await app.keyboard.type("In one sentence, what is RAM?");
  await app.keyboard.press("Enter");
  await expect(app.getByText("Answered").last()).toBeVisible();
});

test("offline: when the runtime is unreachable the UI says so", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __SCAR_ENDPOINT__: unknown }).__SCAR_ENDPOINT__ = { url: "http://127.0.0.1:9", token: "nope" };
  });
  await page.goto("/");
  await expect(page.getByText(/Lost the connection to SCAR's runtime|Reconnecting/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop All" })).toBeDisabled();
});
