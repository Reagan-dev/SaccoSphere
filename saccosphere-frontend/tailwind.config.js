/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,jsx,ts,tsx}",
    "./components/**/*.{js,jsx,ts,tsx}",
  ],
  presets: [require("nativewind/preset")], 
  theme: {
    extend: {},
  },
  fontFamily: {
  heading: ["Poppins-Bold", "sans-serif"],
  body: ["Inter-Regular", "sans-serif"],
  mono: ["FiraCode-Regular", "monospace"],
},
  plugins: [],
}
