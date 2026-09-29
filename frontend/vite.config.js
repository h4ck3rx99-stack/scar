import { defineConfig } from 'vite';

// Relative base so the built app works from file:// inside Electron.
export default defineConfig({
  base: './',
  server: { port: 5173, strictPort: true },
  build: { chunkSizeWarningLimit: 1500 },
});
