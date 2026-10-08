import { expect, test } from "@playwright/test";

test("the service worker never answers API requests from its cache", async ({ request }) => {
  const sw = await (await request.get("/sw.js")).text();
  expect(sw).toContain("denylist");
  expect(sw).toContain("/^\\/api\\//"); // the denylist entry as it appears in the built sw.js
  // No runtime caching routes besides the app-shell navigation fallback.
  expect(sw).not.toContain("NetworkFirst");
  expect(sw).not.toContain("StaleWhileRevalidate");
});
