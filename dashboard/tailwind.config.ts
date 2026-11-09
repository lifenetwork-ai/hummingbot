import type { Config } from 'tailwindcss'

const appColor = (token: string) =>
  `rgb(var(--app-${token}-rgb) / <alpha-value>)`

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        app: {
          bg: appColor('background'),
          surface: appColor('surface'),
          'surface-muted': appColor('surface-muted'),
          nav: appColor('navigation'),
          text: appColor('text-primary'),
          muted: appColor('text-secondary'),
          border: appColor('border'),
          action: appColor('action'),
          'action-hover': appColor('action-hover'),
          accent: appColor('accent'),
          success: appColor('success'),
          danger: appColor('danger'),
        },
        life: {
          primary: 'var(--color-primary)',
          50: 'var(--color-primary50)',
          100: 'var(--color-primary100)',
          200: 'var(--color-primary200)',
          600: 'var(--color-primary600)',
          1000: 'var(--color-primary1000)',
        },
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        heavy: ['Inter', 'IBM Plex Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        button: 'var(--radius-button)',
        card: 'var(--radius-card)',
        sheet: 'var(--radius-sheet)',
        pill: 'var(--radius-pill)',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        report: 'var(--shadow-report)',
        top: 'var(--shadow-top)',
      },
    },
  },
  plugins: [],
} satisfies Config
