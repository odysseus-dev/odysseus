/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#161816",
        paper: "#f7f6f1",
        moss: "#3e5f46",
        clay: "#a4543f",
        tide: "#366a76",
        gold: "#c08b34"
      }
    }
  },
  plugins: []
};
