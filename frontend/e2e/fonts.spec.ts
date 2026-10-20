import { expect, test, type Page } from "@playwright/test";
import { mockBackend } from "./fixtures";

const FACES = ['400 16px "Inter"', '600 16px "Space Grotesk"', '16px "Share Tech Mono"'];

/** Loads every UI font and says, per face, whether it really loaded and
 * whether text in it is visibly different from the fallback font. */
async function fontReport(page: Page) {
  return page.evaluate(async (faces) => {
    await document.fonts.ready;
    const out: Record<string, { loaded: boolean; rendered: boolean }> = {};
    for (const face of faces) {
      const loaded = (await document.fonts.load(face)).some((f) => f.status === "loaded");
      // same weight as the face that was loaded, once with it and once without
      const weight = /^\d{3} /.test(face) ? face.slice(0, 3) : "400";
      const family = face.slice(face.indexOf('"'));
      const probe = document.createElement("span");
      probe.textContent = "Hey Cypher 0123 äöüß";
      probe.style.cssText = `position:absolute;visibility:hidden;font-size:32px;font-weight:${weight};font-family:${family}, serif`;
      const fallback = probe.cloneNode(true) as HTMLSpanElement;
      fallback.style.fontFamily = "serif";
      document.body.append(probe, fallback);
      out[face] = { loaded, rendered: probe.offsetWidth !== fallback.offsetWidth };
      probe.remove();
      fallback.remove();
    }
    return out;
  }, FACES);
}

test("opening the UI sends no request to Google - every font comes from Cypher itself", async ({ page }) => {
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByText("Tippen zum Sprechen")).toBeVisible();
  const report = await fontReport(page);

  for (const face of FACES) expect(report[face], face).toEqual({ loaded: true, rendered: true });
  expect(urls.filter((u) => /google|gstatic/.test(u))).toEqual([]);
  const foreign = urls.filter((u) => !/^(https?|wss?):\/\/(localhost|127\.0\.0\.1)(:\d+)?\//.test(u) && !u.startsWith("data:"));
  expect(foreign).toEqual([]);
  expect(urls.some((u) => /\/assets\/inter-latin-400-normal-[\w-]+\.woff2$/.test(u))).toBe(true);
});

test("offline (service worker, network cut) the UI shows the same fonts", async ({ page, context }) => {
  await mockBackend(page);
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload(); // now the service worker controls the page
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);

  await context.setOffline(true);
  try {
    await page.reload();
    const report = await fontReport(page);
    for (const face of FACES) expect(report[face], face).toEqual({ loaded: true, rendered: true });
  } finally {
    await context.setOffline(false);
  }
});
