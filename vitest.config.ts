import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@decky/ui": fileURLToPath(new URL("./preview/decky-ui.tsx", import.meta.url)) } },
  test: { environment: "jsdom", include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"], restoreMocks: true },
});
