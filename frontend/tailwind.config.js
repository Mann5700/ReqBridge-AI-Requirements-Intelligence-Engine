/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        bg: {
          DEFAULT: '#0a0b10',
          elevated: '#11131a',
          subtle: '#0f1117',
        },
        surface: {
          DEFAULT: '#161a23',
          muted: '#1c2030',
          strong: '#232838',
        },
        border: {
          DEFAULT: '#252a37',
          subtle: '#1d2230',
          strong: '#323849',
        },
        fg: {
          DEFAULT: '#e6e8ee',
          muted: '#9aa3b2',
          subtle: '#646b7a',
        },
        brand: {
          DEFAULT: '#00d4aa',
          50: '#e6fdf7',
          100: '#b5f5e3',
          200: '#7eecce',
          300: '#46e3b8',
          400: '#1fdba8',
          500: '#00d4aa',
          600: '#00a888',
          700: '#007e66',
          800: '#005544',
          900: '#002b22',
        },
        accent: {
          violet: '#8b5cf6',
          fuchsia: '#d946ef',
          amber: '#f59e0b',
          rose: '#f43f5e',
          sky: '#38bdf8',
        },
        ado: { blue: '#0078d4' },
        // Keep slate/teal for legacy classes already in pages
        slate: { 950: '#0f1117' },
        teal: { 500: '#00d4aa' },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['"Inter Display"', 'Inter', 'sans-serif'],
        mono: ['"Geist Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(0,212,170,0.4), 0 0 24px -4px rgba(0,212,170,0.35)',
        card: '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.6)',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(ellipse at top, var(--tw-gradient-stops))',
        'grid-dark':
          'linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px)',
      },
      backgroundSize: { grid: '32px 32px' },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.35s ease-out both',
        shimmer: 'shimmer 2.4s linear infinite',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};
