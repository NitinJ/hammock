/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0a0e1a",
          raised: "#0f1424",
          elevated: "#161c2f",
        },
        accent: {
          DEFAULT: "#7c3aed",
          soft: "#a78bfa",
          glow: "rgba(124, 58, 237, 0.35)",
        },
        text: {
          primary: "#f3f4f6",
          secondary: "#9ca3af",
          tertiary: "#6b7280",
        },
        border: {
          DEFAULT: "rgba(255, 255, 255, 0.08)",
          strong: "rgba(255, 255, 255, 0.16)",
        },
        state: {
          pending: "#6b7280",
          running: "#06b6d4",
          succeeded: "#10b981",
          failed: "#ef4444",
          awaiting: "#f59e0b",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      backdropBlur: {
        xs: "2px",
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(124, 58, 237, 0.4), 0 0 24px rgba(124, 58, 237, 0.15)",
      },
    },
  },
  plugins: [],
};
