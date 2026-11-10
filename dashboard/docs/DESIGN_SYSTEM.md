# LifeAI Frontend Design System

This project intentionally preserves the visual system from the supplied LifeAI
frontend while removing all original product features.

## Source of truth

`src/theme/_config.ts` → `scripts/generate-theme.ts` → `src/theme/generated/tokens.css` → Tailwind → shared UI components.

Change tokens in `_config.ts`, then run `pnpm run theme:generate`. Do not edit generated CSS manually.

## Colors

- Light action: `#0076F5`; dark action: `#429DFF`.
- Light background `#FFFFFF`, dark background `#141416`.
- Light surface `#FFFFFF`, dark surface `#1B1A23`.
- Semantic aliases: `app-bg`, `app-surface`, `app-surface-muted`, `app-text`, `app-muted`, `app-border`, `app-action`, `app-action-hover`, `app-accent`, `app-success`, `app-danger`.
- Raw palette tokens remain available for exceptional decorative purposes.

## Typography

- IBM Plex Sans: UI and body text.
- Inter 800/900: display headlines (`font-heavy`).
- Distinctive display tracking: `-0.055em` / `-0.06em` for large hero text.
- Body copy typically 16–18px with comfortable line height.

## Radius and elevation

- Button 5px, card 10px, sheet 16px, pill 30px.
- Card shadow: `0 3px 12px rgba(47,86,201,0.20)`.
- Prefer subtle borders over heavy shadows.

## Visual composition

- Light backgrounds, deliberate white space and a strong typographic hierarchy.
- Restrained blue accents; avoid overusing gradients, glow or decorative animation.
- Responsive page gutters: mobile 20px, tablet 40px, desktop 64px.
- Main content maximum around 1440px.
- Header fixed at 76px with translucent background and backdrop blur.
- Optional subtle radial tint and 48px grid ornament in editorial hero regions.

## UI components

`Button`, `Surface`, `SectionLabel`, `Input`, `Badge`, `Dialog`, `EmptyState`,
`AnimatedProgress`, `BrandLogo`, `MotionPage`.

See `/ui-kit` when running the app for a living preview.

## Theme

`ThemeProvider` exposes `theme`, `setTheme`, `toggleTheme` via `useTheme()`.
The choice is stored in localStorage; style variants are provided by CSS variables
and Tailwind `dark` class. Default is light. All components must be valid in both modes.

## Accessibility

- Visible focus states, labels and helper text for controls.
- Motion respects `prefers-reduced-motion`.
- Dialog is dismissible with Escape; for production-grade complex dialogs, add focus trapping and return-focus management or use a vetted accessible dialog primitive.
- Verify keyboard and screen-reader behavior when introducing new components.

## System feedback

See [System feedback](FEEDBACK.md) for the implemented Sonner-based toast foundation (`ToastHost.tsx`),
typed `notify` adapter (`src/core/feedback/notify.ts`), shared `Banner` and `InlineFeedback` primitives,
and UI Kit live interactive samples.
