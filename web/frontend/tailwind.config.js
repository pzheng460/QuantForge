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
        tv: {
          bg:       '#131722',
          panel:    '#1e222d',
          border:   '#2a2e39',
          text:     '#d1d4dc',
          muted:    '#787b86',
          green:    '#26a69a',
          red:      '#ef5350',
          blue:     '#2962ff',
          'blue-hover': '#1e53e5',
        },
      },
    },
  },
  plugins: [],
}
