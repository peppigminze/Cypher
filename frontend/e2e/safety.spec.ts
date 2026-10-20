import { expect, test } from "@playwright/test";
import { mockBackend } from "./fixtures";

/** "Sicherheitsabfrage für kritische Aktionen" (FIX2): off only after a warning,
 * and while it is off the HUD says so on every screen. */
test("turning the safety prompt off needs a warning and shows a HUD badge", async ({ page }) => {
  await mockBackend(page);
  let state = { enabled: true, mode: "on", red_line: ["format"], red_line_labels: ["Formatieren / Partitionieren"] };
  const puts: unknown[] = [];
  await page.route("**/api/security/critical-confirm", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { enabled: boolean; scope: string };
      puts.push(body);
      state = { ...state, enabled: body.enabled, mode: body.enabled ? "on" : body.scope === "session" ? "off_session" : "off_permanent" };
    }
    await route.fulfill({ json: state });
  });
  await page.route("**/api/settings/voice", (route) => route.fulfill({ json: { followup_window_seconds: 10 } }));
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        llm_provider: "local",
        local_llm_model: "qwen3.5:9b",
        local_llm_base_url: "http://localhost:11434",
        cloud_llm_enabled: false,
        cloud_llm_model: null,
        allowed_directories: [],
        wake_on_lan: { configured: false, mac_address: null, broadcast: "255.255.255.255", port: 9, command: null },
      },
    })
  );
  const zero = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, request_count: 0 };
  await page.route("**/api/usage", (route) =>
    route.fulfill({ json: { today: { total: zero, by_provider: {} }, month: { total: zero, by_provider: {} } } })
  );

  await page.goto("/");
  await expect(page.getByText("⚠ ABFRAGE AUS")).toHaveCount(0);

  await page.getByRole("button", { name: "Menü öffnen (Taste M)" }).click();
  await page
    .getByRole("button", { name: /Einstellungen/ })
    .first()
    .click();
  const section = page.getByRole("region", { name: "Sicherheitsabfrage für kritische Aktionen" });
  await expect(section.getByRole("status")).toContainText("AN");

  await section.getByRole("button", { name: "Ausschalten …" }).click();
  await expect(section.getByRole("alertdialog")).toContainText("Wirklich ohne Nachfrage?");
  expect(puts).toEqual([]); // nothing changed before the explicit choice
  await section.getByRole("button", { name: "Nur für diese Sitzung" }).click();

  await expect(section.getByRole("status")).toContainText("AUS bis zum nächsten Neustart");
  expect(puts).toEqual([{ enabled: false, scope: "session" }]);
  await expect(page.getByText("⚠ ABFRAGE AUS")).toBeVisible();

  await section.getByRole("button", { name: "Wieder einschalten" }).click();
  await expect(page.getByText("⚠ ABFRAGE AUS")).toHaveCount(0);
});
