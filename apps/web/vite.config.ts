import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Static SPA served as Cloudflare Workers static assets (spec D-3; wrangler.jsonc). The backend is a separate origin
// (evals-api.entropic.science); in dev, /api is proxied to the local backend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8830", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
