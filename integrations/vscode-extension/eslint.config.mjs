// Flat ESLint config for the Odysseus VS Code extension (ESLint 9+/typescript-eslint).
// Lints the TypeScript sources in src/ (extension host) and webview/ (browser).
// Formatting is owned by Prettier — eslint-config-prettier disables stylistic
// rules that would conflict.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import prettier from "eslint-config-prettier";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "esbuild.js", "*.config.*", "**/*.d.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  prettier,
  {
    files: ["src/**/*.ts", "webview/**/*.ts"],
    rules: {
      // TypeScript already resolves identifiers; no-undef is noise for TS.
      "no-undef": "off",
      // The chat/SSE payloads are dynamically typed JSON — `any` is intentional.
      "@typescript-eslint/no-explicit-any": "off",
      // Let underscore-prefixed args/vars be intentionally unused.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
      ],
      // Empty catch blocks are used deliberately (best-effort cleanup).
      "no-empty": ["warn", { allowEmptyCatch: true }],
    },
  },
);
