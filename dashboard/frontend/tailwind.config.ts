import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

export default {
  content: ["./index.html", "./src/**/*.{vue,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // State badge colours — all state machines use these
        "state-running": "#3b82f6",     // blue-500
        "state-attention": "#f59e0b",   // amber-500
        "state-succeeded": "#22c55e",   // green-500
        "state-failed": "#ef4444",      // red-500
        "state-terminal": "#6b7280",    // gray-500
        "state-submitted": "#8b5cf6",   // violet-500
        // Cost warning thresholds
        "cost-ok": "#22c55e",
        "cost-warn": "#f59e0b",
        "cost-over": "#ef4444",
        // Brand surface
        surface: "#0f172a",            // slate-900
        "surface-raised": "#1e293b",   // slate-800
        "surface-highlight": "#334155", // slate-700
        border: "#334155",
        "text-primary": "#f8fafc",
        "text-secondary": "#94a3b8",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "Cascadia Code", "ui-monospace", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [typography],
} satisfies Config;
