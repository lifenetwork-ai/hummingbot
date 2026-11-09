# UX and Motion Rules

The visual and interaction language is preserved from the original LifeAI UI, but no vesting or wallet behavior remains.

## Interaction principles

- Prioritize the most important content and primary action on a page.
- Use progressive disclosure; avoid deeply nested cards, walls of text, decorative badges and excessive controls.
- Keep surface feedback subtle: hover elevation around -2px, border emphasis and a short shadow transition (160–220ms).
- Use clear loading, success, empty and error states. Never fabricate data to make a loading screen look complete.
- For mobile, maintain 44px minimum touch targets and keep navigation in a compact, accessible menu.
- Keep one primary action per content region where possible.

## Motion

- Page entrance: fade from opacity 0 to 1 with translateY 8px; approximately 300ms.
- Modal entrance and exit should be brief and non-blocking.
- Progress bars should animate only toward the latest known value; do not continuously animate invented data.
- Use standard entrance easing `[0.16, 1, 0.3, 1]`.
- Follow `prefers-reduced-motion`; disable movement when requested.

## Components

- `MotionPage` for route composition.
- `AnimatedProgress` for generic progress values in the 0–100 range.
- `Button`, `Dialog`, `Input`, `Badge`, `Surface`, `EmptyState` for consistent feedback.
- `AppShell` owns global header, responsive navigation, theme toggle and footer.

## Accessibility

- Never use color alone to represent state.
- Use semantically named links, buttons, and form labels.
- Visible keyboard focus is required for every interactive control.
- Close modals with Escape and return focus to the triggering element.
- Use ARIA for progress and inputs where necessary; prefer native semantics first.
