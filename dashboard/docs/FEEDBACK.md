# System feedback

## Scope and implementation status

This document defines the implementation contract for shared user feedback.
The selected approach is Sonner for toast mechanics, LifeAI styling, a thin typed
notification adapter, and application-owned banner and inline primitives.

Status: Implemented and verified in UI Kit (`/ui-kit`).
- Host: `src/shared/components/ToastHost.tsx` mounted once in `src/app/AppProviders.tsx`.
- Adapter: `src/core/feedback/notify.ts`.
- Primitives: `src/shared/components/Banner.tsx` and `src/shared/components/InlineFeedback.tsx`.
- Auth integration: `src/features/auth/AuthAlert.tsx` delegates to `Banner` while preserving semantics.

Notification history, unread counts, delivery channels and backend event storage
are outside this foundation.

## Choose the feedback surface

| Surface | Purpose | Lifetime |
| --- | --- | --- |
| Inline | Field validation or an error tied to a form/action | Until corrected or superseded |
| Toast | Brief feedback for an explicit user action | Temporary, or persistent when action is needed |
| Banner | An ongoing condition affecting a section or the application | Until resolved; dismiss only by explicit policy |

Choose one primary announcement for an event. Do not repeat an inline error in a
toast. Keep ongoing failures visible at their affected surface. Polling failures
update that state instead of creating a toast on every request. Routine refresh
success and reconnection do not generate success toasts by default.

## Toast contract

- Mount one host in the application provider layer so navigation does not duplicate it.
- Use Sonner for timers, stacking and lifecycle; verify the compatible version and
  supported options against official documentation before installation.
- Put the thin adapter in `src/core/feedback/` and visual primitives in
  `src/shared/components/`. Features import the adapter rather than Sonner.
  Shared components never import feature or page code.
- Implemented adapter API (`src/core/feedback/notify.ts`):
  `notify.success(title, options?)`, `notify.error(title, options?)`,
  `notify.info(title, options?)`, `notify.warning(title, options?)`,
  `notify.loading(title, options?)`, `notify.update(id, options)`, `notify.dismiss(id?)`.

  Example:
  ```ts
  import { notify } from '../core/feedback/notify'

  // Brief feedback for user action (5s default)
  notify.success('Profile saved', { description: 'Trading parameters updated.' })

  // Async lifecycle with stable ID (does not duplicate)
  notify.loading('Submitting order…', { id: 'order-1' })
  notify.update('order-1', {
    type: 'success',
    title: 'Order submitted',
    description: 'Exchange accepted order.',
  })
  ```
- A stable ID identifies one operation/event. Updating it replaces that toast;
  independent IDs stay independent. Do not deduplicate by message text.
- Success/info default to about five seconds. Warning/error requiring action
  persist until closed or resolved. Loading persists until explicitly updated or dismissed.
  The feature owns completion and cleanup; do not leave abandoned loading toasts.
- Provide close controls except for loading, and pause timed dismissal on hover
  and keyboard focus. Verify actual library behavior rather than assuming support.
- Show at most three toasts concurrently. Use bottom-right on desktop and a
  bottom placement with gutters and safe-area spacing on mobile. Avoid covering
  essential controls; verify long content and narrow viewports.
- Use semantic LifeAI tokens, restrained status accents, icons and readable text
  in both themes. No hardcoded palette or decorative animation. Add missing tokens
  through the theme source/generator only when necessary.
- Do not steal focus. Label icon controls, support keyboard actions, respect
  reduced motion, and verify live announcements using the real rendered library.
  Do not attach an additional live region that announces the same toast twice.

## Banner and inline contract

Keep these primitives domain-neutral with a small typed API: variant, title,
optional description/action and an optional dismiss control where appropriate.
Use existing button and token conventions. Persistent unresolved system problems
are non-dismissible by default. Dismissal affects presentation only.
Associate field errors with inputs. Use polite status announcements for ordinary
updates and assertive alerts only when urgency warrants interruption. Stable
conditions must not be announced again on every render or poll.

## Async operations and content

The feature owns business meaning, retry safety and event identity. A successful
request confirms only what its API contract guarantees. Distinguish submission,
acceptance, actual completion and unknown outcome. Do not automatically equate a
resolved promise with completion of a background operation.
A timeout after a mutation may leave its outcome unknown. Offer status checking
or retry only when the feature's contract establishes that it is safe. The adapter
must not retry network calls or globally toast every HTTP/Query error.
Use short, actionable English copy. Do not expose raw backend errors, stack traces,
credentials or sensitive details. Generic primitives do not invent product messages.

## Implementation acceptance

Before implementation, compare existing tests with the applicable cases below.

| Area | Required evidence |
| --- | --- |
| Lifecycle | Create, update by ID, dismiss, timed/persistent behavior, loading success/failure/cleanup |
| Identity | Repeated ID updates one toast; independent IDs remain separate; visible limit |
| Side effects | Actions run only on interaction; no automatic mutation retries; banner dismissal has no business effect |
| App lifecycle | One host across route changes; defined behavior on unmount/reload; no promise of persisted history |
| Accessibility | Keyboard close/action, focus preservation, hover/focus timing, appropriate non-duplicate announcements |
| Presentation | Mobile safe areas, long text, both themes, reduced motion, essential controls remain reachable |
| Integration | Polling does not spam; existing form/auth behavior remains intact when migrated |

Use component tests for adapter/component behavior and real-browser tests for
library interaction, timing, layout and accessibility. Mocking Sonner does not
verify Sonner itself. Follow repository quality gates and report PASS/FAIL/SKIP
with commands and evidence. Review visual diffs before accepting baselines.

Add manually triggered, explicitly labeled examples in UI Kit for variants,
loading transitions, dismissal, repeated/independent IDs, long text, actions,
persistent/dismissible banners and inline errors. Do not trigger demos on page load.
Implementing this contract does not authorize backend integration or operational actions.
