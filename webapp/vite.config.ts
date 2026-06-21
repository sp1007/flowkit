import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API = "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true, ws: true },
      "/media": { target: API, changeOrigin: true },
      // Assembled outputs (final.mp4, timeline.xml, captions.srt). Without this the dev
      // server returns the SPA index.html for these paths, so a downloaded timeline.xml is
      // actually HTML → DaVinci "DOM parser error. Line 1".
      "/studio-media": { target: API, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
