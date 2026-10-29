import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@decky/ui": fileURLToPath(new URL("./preview/decky-ui.tsx", import.meta.url)),
    "@decky/api": fileURLToPath(new URL("./preview/decky-api.ts", import.meta.url)) } },
  build: { outDir: "preview-dist" },
  server: { host: "127.0.0.1", port: 5178, strictPort: true },
});
