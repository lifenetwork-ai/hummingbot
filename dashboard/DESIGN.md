# Design

## Source of truth
Active — 2026-11-10. Applies to dashboard authentication. Evidence: AGENTS.md,
docs/DESIGN_SYSTEM.md, docs/UX_MOTION.md, docs/API.md, ../docs/fe-integration.md,
existing AppShell, shared Button and BrandLogo, backend access contract.
No login screenshot baseline exists.

## Brand
Preserve LifeAI typography, semantic tokens and light/dark themes. Calm operational
interface; avoid marketing claims and fabricated observations.

## Product goals
Approved operators can sign in, reach protected routes and sign out. Access is
confirmed by the backend before displaying protected content. No new backend
session or refresh-token contract is introduced.

## Personas and jobs
Invited market-making operators accessing the dashboard on desktop or mobile.

## Information architecture
/login and /ui-kit are public. / requires authentication. Deep links retain a safe
local return destination. Login and protected content share the theme header.

## Design principles
One primary action: the official Google sign-in button. Show configuration,
loading, denied access and network failure states explicitly.

## Visual language
Reuse semantic theme tokens, existing type scale, spacious single-column layout,
brand logo and subtle MotionPage entrance. No new tokens or decorative imagery.

## Components
GoogleSignIn owns official SDK presentation. LoginPage composes the login screen.
`app/ProtectedRoute` gates content. `core/auth/AuthProvider` owns in-memory identity and credential.
Existing Button handles retry/sign-out; AppShell owns theme and navigation.

## Accessibility
Semantic heading, live loading/error feedback, keyboard-operable Google button,
visible focus and reduced-motion support. Existing palette contrast limitations
remain documented in docs/TESTING.md; no WCAG certification is implied.

## Responsive behavior
Single column with mobile gutters, 280px Google button and bounded text width.
Theme and sign-out remain accessible from the shared header.

## Interaction states
Anonymous → authenticating → authenticated. Failure returns to anonymous with
an error and retry. Logout, expiry and unauthorized API responses clear the session.
Reload requires sign-in because tokens are never persisted. Backend has no refresh
endpoint; reconnect uses a fresh Google credential, without replaying mutations.

## Content voice
Short English utility copy consistent with the existing dashboard.

## Implementation constraints
React, TanStack Router/Query, existing design system; no new dependencies. Google
SDK follows the repository's scripts/google_login_local/app.js integration.
Backend validates signature, audience and current access. Client route gates are
for navigation; backend remains the authorization boundary.

## Open questions
- [ ] Deployment owner: configure actual Google web client ID, permitted JavaScript
  origins, backend audience, CORS and public gateway before live acceptance.
- [ ] Design owner: review login screenshots and establish Linux visual baselines.

## Shared system feedback

The shared feedback implementation contract is [System feedback](docs/FEEDBACK.md).
It extends this design beyond authentication without changing the auth contract.
Status: Implemented and active. Sonner provides toast mechanics with LifeAI semantic
tokens (`ToastHost.tsx` mounted once in `AppProviders.tsx`). Features trigger
notifications through the thin typed adapter `src/core/feedback/notify.ts`.
`Banner.tsx` and `InlineFeedback.tsx` provide shared visual primitives, and `AuthAlert.tsx`
delegates to `Banner.tsx` preserving existing semantics and tests.

For market-making operations, distinguish “Stop request sent” from “Bot stopped”.
Only confirmed runtime state establishes that the bot stopped. A mutation timeout
uses an unknown-outcome state until reconciled; do not automatically resend it.
Bot API integration and operational notification delivery are separate feature work.
