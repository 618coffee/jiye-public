/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"PingFang SC"',
          '"Helvetica Neue"', '"Microsoft YaHei"', 'sans-serif',
        ],
      },
      colors: {
        ink: {
          50:  '#f7f7f8',
          100: '#eeeef0',
          200: '#d9d9de',
          300: '#b6b6bf',
          400: '#84848f',
          500: '#5a5a64',
          600: '#3f3f47',
          700: '#2c2c33',
          800: '#1c1c22',
          900: '#0f0f12',
        },
        rouge: {
          50:  '#fef2f4',
          100: '#fde6ea',
          200: '#fbd0d9',
          300: '#f7a8b8',
          400: '#f17692',
          500: '#e64972',
          600: '#d22a5d',
          700: '#b01b4d',
          800: '#931a47',
          900: '#7c1a42',
        },
      },
      boxShadow: {
        'soft': '0 1px 2px rgba(15,15,18,0.04), 0 8px 24px rgba(15,15,18,0.06)',
        'lift': '0 4px 12px rgba(15,15,18,0.08), 0 16px 48px rgba(15,15,18,0.10)',
      },
      keyframes: {
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
};
