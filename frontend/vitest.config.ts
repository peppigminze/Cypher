import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts (which registers the PWA plugin) so
// running tests never touches the service-worker build.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    // e2e/ is Playwright (npm run test:e2e), not Vitest.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
