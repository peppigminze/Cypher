import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
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
});
