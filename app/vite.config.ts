import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

// The same policy Tauri applies to the webview (src-tauri/tauri.conf.json). Built pages have no inline scripts, so
// script-src stays 'self'; the only network peer is the local runtime on 127.0.0.1.
export const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* ipc: http://ipc.localhost",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

function csp(): Plugin {
  return {
    name: "scar-csp",
    apply: "build",
    transformIndexHtml(html) {
      return html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`);
    },
  };
}

// Dev only (`vite dev`, never in builds): hand the running runtime's endpoint to the page, so the UI can be developed
// in a browser. Reads <data dir>/app-api.json, which only the current user can read.
function devEndpoint(): Plugin {
  return {
    name: "scar-dev-endpoint",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/__scar_endpoint", (_req, res) => {
        const dataDir = process.env.SCAR_DATA_DIR || resolve(process.env.LOCALAPPDATA ?? "", "SCAR");
        try {
          const ep = JSON.parse(readFileSync(resolve(dataDir, "app-api.json"), "utf-8")) as { url: string; token: string };
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ url: ep.url, token: ep.token }));
        } catch {
          res.statusCode = 404;
          res.end("{}");
        }
      });
    },
  };
}

// Three windows, one bundle graph: the main window, the Quick Bar and the screen-control indicator.
export default defineConfig({
  plugins: [react(), csp(), devEndpoint()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, host: "localhost" },
  preview: { port: 1420, strictPort: true, host: "localhost" },
  build: {
    target: "es2022",
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, "index.html"),
        quickbar: resolve(import.meta.dirname, "quickbar.html"),
        indicator: resolve(import.meta.dirname, "indicator.html"),
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
  },
});
