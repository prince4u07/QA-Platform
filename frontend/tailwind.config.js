/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Base surfaces (Indigo / Teal dark theme)
        base: "#0d1117",
        surface: "#161b22",
        "surface-2": "#1c232c",
        // Brand accents
        brand: {
          DEFAULT: "#6366f1",
          indigo: "#6366f1",
          teal: "#14b8a6",
          sky: "#38bdf8",
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        display: ['"Space Grotesk"', 'Inter', 'system-ui', 'sans-serif'],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      boxShadow: {
        glow: "0 0 24px -2px rgba(99,102,241,0.45)",
        "glow-teal": "0 0 24px -2px rgba(20,184,166,0.40)",
        "glow-sky": "0 0 24px -2px rgba(56,189,248,0.40)",
        card: "0 8px 30px rgba(0,0,0,0.35)",
      },
      backgroundImage: {
        "brand-gradient": "linear-gradient(135deg, #6366f1 0%, #14b8a6 100%)",
        "brand-radial":
          "radial-gradient(60% 60% at 30% 20%, rgba(99,102,241,0.25) 0%, transparent 60%), radial-gradient(50% 50% at 80% 80%, rgba(20,184,166,0.18) 0%, transparent 60%)",
      },
      keyframes: {
        "mesh-drift": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(2%, -2%, 0) scale(1.08)" },
        },
        "glow-pulse": {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "mesh-drift": "mesh-drift 18s ease-in-out infinite",
        "glow-pulse": "glow-pulse 4s ease-in-out infinite",
        "fade-up": "fade-up 0.4s ease-out both",
      },
    },
  },
  plugins: [],
};
