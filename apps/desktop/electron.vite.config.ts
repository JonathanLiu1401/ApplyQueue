import react from "@vitejs/plugin-react";
import { defineConfig } from "electron-vite";

// All runtime deps are devDependencies and get bundled, so the packaged app ships no node_modules.
export default defineConfig({
  main: { build: { externalizeDeps: false } },
  // Sandboxed preloads must be CommonJS.
  preload: {
    build: {
      externalizeDeps: false,
      rollupOptions: { output: { format: "cjs", entryFileNames: "[name].cjs" } },
    },
  },
  renderer: { plugins: [react()] },
});
