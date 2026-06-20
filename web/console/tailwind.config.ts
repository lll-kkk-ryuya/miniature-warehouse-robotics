import type { Config } from "tailwindcss";

// Capture skin (doc22 §12.4): high-contrast dark surface, large type, fixed grid. Tuned for
// OBS/YouTube capture and operator legibility; per-component styling stays minimal.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./providers/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: { DEFAULT: "#0b0f17", panel: "#131a26", raised: "#1c2636" },
        accent: { DEFAULT: "#3b82f6", bot1: "#38bdf8", bot2: "#f59e0b" },
        ok: "#22c55e",
        warn: "#eab308",
        danger: "#ef4444",
      },
      fontFamily: {
        // CJK-first: 日本語 UI 文言の wrap/legibility を優先（doc22:288）
        sans: ["system-ui", "-apple-system", "Hiragino Sans", "Noto Sans JP", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
