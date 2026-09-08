/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        base: "#161b22",
        surface: "#1d232c",
        "surface-2": "#232b36",
        // Single flat accent + legacy aliases so old class names keep working.
        // All aliases resolve to the same solid blue — no gradients.
        brand: {
          DEFAULT: "#4c8dff",
          indigo: "#4c8dff",
          teal: "#4c8dff",
          sky: "#7aa8ff",
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        // Kept as no-ops so old `shadow-glow` classes don't break.
        // New code should use `shadow-card` or nothing.
        glow: "none",
        "glow-teal": "none",
        "glow-sky": "none",
        card: "0 1px 2px rgba(0,0,0,0.4)",
      },
      backgroundImage: {
        // Flat aliases — old gradient classes render as solid fills.
        "brand-gradient": "none",
        "brand-radial": "none",
      },
      keyframes: {},
      animation: {},
    },
  },
  plugins: [],
};
