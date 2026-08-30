import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    // `@/api/client` rather than `../../../api/client`. Relative imports that
    // climb three levels are the ones that break silently when a file moves.
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Fail loudly if 5173 is taken rather than silently moving to 5174 — the
    // backend's CORS allowlist names this exact origin, so a "helpful" port
    // change turns into an unexplained CORS error.
    strictPort: true,
  },
  build: {
    rollupOptions: {
      output: {
        // Split the big, stable dependencies into their own chunks.
        //
        // Recharts (with its d3 internals) is roughly two thirds of the bundle
        // and changes only when it is upgraded. In one file with the app code,
        // every deploy invalidates all of it and returning users re-download
        // ~200 kB gzipped to get a one-line fix. Split out, the app chunk is
        // small and the vendor chunks stay in cache across deploys.
        //
        // Not code-splitting by route instead, and that is deliberate: the
        // charts are on the landing screen, so lazy-loading them would only
        // move the same bytes behind a spinner the user waits for anyway.
        manualChunks: {
          recharts: ["recharts"],
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },
});
