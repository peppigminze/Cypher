import { defineConfig } from "@playwright/test";

/**
 * Smoke test of the BUILT app (M6). The backend is simulated with route and
 * WebSocket mocks (e2e/fixtures.ts), so the test needs neither Ollama nor
 * a microphone. Runs in the Microsoft Edge that ships with Windows
 * (channel "msedge") - no extra browser download.
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4174",
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "msedge",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npx vite build --mode e2e --outDir dist-e2e && npx vite preview --outDir dist-e2e --port 4174 --strictPort",
    url: "http://localhost:4174",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
