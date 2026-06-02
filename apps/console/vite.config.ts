import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { TanStackRouterVite } from "@tanstack/router-plugin/vite";
import path from "node:path";

// BrickVision Console Vite config.
//
// Per docs/12-visual-builder.md §10.2 (REVISED v0.7.6.9):
//   - Hashed-chunk SPA build into apps/console/dist/
//   - Code-splitting via TanStack Router's lazy() per-route
//   - Single chunk above 500 KB gzipped fails the build
//     (so the per-route lazy() discipline is enforced)
//   - Dev proxy forwards /api/* + /ws/* to the FastAPI sidecar
//     so dev-mode UC OBO context flows through the same path
//     as production.

export default defineConfig({
  plugins: [
    TanStackRouterVite(),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: "es2022",
    outDir: "dist",
    sourcemap: true,
    chunkSizeWarningLimit: 500,
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
          "router-vendor": ["@tanstack/react-router", "@tanstack/react-query"],
          "canvas-vendor": ["reactflow"],
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Both HTTP routes and the build-event WebSocket
      // (/api/builds/{id}/stream) live under /api, so we enable
      // ws: true here to handle Upgrade requests on the same prefix.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
