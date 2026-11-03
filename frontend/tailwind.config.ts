import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#e8eef7",
        muted: "#8ea3ba",
        paper: "#06111f",
        warm: "#101b2d",
        line: "#26364f",
        teal: "#2dd4bf",
        emerald: "#34d399",
        amber: "#fbbf24",
        rose: "#fb7185"
      },
      boxShadow: {
        panel: "0 22px 55px rgba(0, 0, 0, 0.35)"
      }
    }
  },
  plugins: []
} satisfies Config;

