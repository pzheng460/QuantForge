/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#eef2ff',
          500: '#667eea',
          600: '#5a67d8',
          700: '#4c51bf',
        },
      },
    },
  },
  plugins: [],
}
