import { expect, test } from "@playwright/test";
import { OLLAMA_DOWN, mockBackend } from "./fixtures";

test("the voice view loads, connects and answers a typed command", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByText("CYPHER", { exact: true })).toBeVisible();
  await expect(page.getByText("Tippen zum Sprechen")).toBeVisible();
  const input = page.getByRole("textbox", { name: "Befehl tippen" });
  await input.fill("Wie spät ist es?");
  await input.press("Enter");
  await expect(page.getByText("Es ist zwölf Uhr.")).toBeVisible();
  await expect(page.getByText("„Wie spät ist es?“")).toBeVisible();
  await expect(page.getByLabel("Status")).toContainText("0,8 S");
});

test("a broken component shows a designed error state", async ({ page }) => {
  await mockBackend(page, OLLAMA_DOWN);
  await page.goto("/");
  const banner = page.getByRole("alert").filter({ hasText: "Ollama läuft nicht." });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Sprachmodell-Server");
});

test("the hidden menu opens with M, shows diagnostics and closes with Esc", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByText("Tippen zum Sprechen")).toBeVisible();
  await page.keyboard.press("m");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /Diagnose/ }).click();
  await expect(dialog.getByText("Sprechende → erste Silbe")).toBeVisible();
  await expect(dialog.getByText("3653 von 8192 Tokens", { exact: false })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("works at phone width without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: /sprechen/i })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
