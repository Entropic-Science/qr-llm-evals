import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Many tests draw a whole page in jsdom. Alone each takes about a second; with every worker busy the first
    // draw of a file can pass the default five seconds, which failed runs in which nothing was wrong.
    testTimeout: 20_000,
  },
});
