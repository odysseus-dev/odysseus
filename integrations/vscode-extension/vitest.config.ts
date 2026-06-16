import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests run in plain Node (no extension host), so the real `vscode` module
// doesn't exist — alias it to a tiny stub. We only test vscode-free pure logic
// (SSE parsing, URL normalization); the stub just lets those modules import.
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
  resolve: {
    alias: {
      vscode: fileURLToPath(new URL("./test/vscode-stub.ts", import.meta.url)),
    },
  },
});
