/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        oxblood: "#4A1520",
        paper: "#FBF9F6",
        ink: "#1A1614",
        clay: "#8B7355",
        approve: "#2D6A4F",
        review: "#B45309",
      },
      opacity: { 4: "0.04", 6: "0.06", 8: "0.08", 12: "0.12", 15: "0.15", 35: "0.35", 45: "0.45" },
      fontFamily: {
        serif: ["Instrument Serif", "Fraunces", "Georgia", "serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
