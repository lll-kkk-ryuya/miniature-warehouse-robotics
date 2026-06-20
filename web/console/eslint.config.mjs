import js from "@eslint/js";
import tseslint from "typescript-eslint";
import globals from "globals";

// Flat config for the web-quality CI job (doc22:348). Uses typescript-eslint directly (the
// eslint-config-next preset crashes under eslint 9 + FlatCompat — circular plugin config); the
// governance PR that enables web-quality can layer @next/eslint-plugin-next rules on top.
export default tseslint.config(
  { ignores: ["out/**", ".next/**", "node_modules/**", "next-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: { globals: { ...globals.browser } },
  },
);
