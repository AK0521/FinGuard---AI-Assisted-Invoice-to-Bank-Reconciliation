import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  cacheDir: "../.vite-cache/frontend",
  server: {
    port: 5173,
  },
});

