import { existsSync } from "node:fs";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

/** Startup checks for two env pitfalls (printed by dev server and build):
 *  - frontend/.env.local silently overrides frontend/.env
 *  - VITE_USER_TOKEN is obsolete; a token must never be baked into the bundle */
function warnAboutEnvPitfalls(mode: string): void {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  if (existsSync(".env.local")) {
    console.warn("[cypher] Hinweis: frontend/.env.local existiert und überschreibt Werte aus frontend/.env.");
  }
  if (env.VITE_USER_TOKEN) {
    console.warn(
      "[cypher] VITE_USER_TOKEN ist veraltet und wird ignoriert. Bitte aus frontend/.env entfernen - " +
        "die Oberfläche holt den Token selbst (am PC) bzw. per Kopplungscode (Handy)."
    );
  }
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  warnAboutEnvPitfalls(mode);
  return {
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      // The service worker caches only the app shell (HTML/JS/CSS/icons).
      // API answers are never cached - stale health, memory or audit data
      // would be worse than an honest "nicht erreichbar" - and navigations
      // to backend paths (e.g. /api/docs) must reach the backend, not the
      // cached index.html. Checked by e2e/pwa.spec.ts.
      workbox: {
        // App shell incl. the bundled fonts (FIX2): offline the UI looks the same.
        globPatterns: ["**/*.{js,css,html,woff2}"],
        navigateFallbackDenylist: [/^\/api\//, /^\/health$/, /^\/speak$/, /^\/transcribe$/],
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
      },
      includeAssets: ["favicon.svg"],
      manifest: {
        // All-caps is deliberate - matches the Sidebar/<title> brand
        // style established in the Phase 0 rebrand, not an oversight.
        name: "CYPHER",
        short_name: "CYPHER",
        description: "Local-first personal AI agent",
        theme_color: "#05070d",
        background_color: "#05070d",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            // Android adaptive-icon safe zone (content within the
            // center ~80%) - the existing ring/orb artwork already
            // sits comfortably inside that, so the same file works
            // as-is without a dedicated maskable export.
            src: "icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
  },
  };
});
