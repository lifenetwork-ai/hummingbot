# UX and Motion Rules

The visual and interaction language is preserved from the original LifeAI UI, but no vesting or wallet behavior remains.

## Interaction principles

- Prioritize the most important content and primary action on a page.
- Use progressive disclosure; avoid deeply nested cards, walls of text, decorative badges and excessive controls.
- Keep surface feedback subtle: hover elevation around -2px, border emphasis and a short shadow transition (160–220ms).
- Use clear loading, success, empty and error states. Never fabricate data to make a loading screen look complete.
- For mobile, maintain 44px minimum touch targets and keep navigation in a compact, accessible menu.
- Keep one primary action per content region where possible.

## Cursor pagination

Any long list backed by an endpoint that returns `next_cursor` (commands, events, audit, orders, fills, accounts) must paginate through the shared `CursorPagination` component (`src/shared/components/CursorPagination.tsx`) instead of a hand-rolled button. The "Load more" button renders only while more pages remain; no extra caption is shown next to it.

- Default page size is **10** (`DEFAULT_HISTORY_PAGE_SIZE` in `src/features/history/filters.ts`); pass `limit` explicitly and let "Reset filters" restore it rather than dropping it back to the backend default of 100.
- Wire `hasMore`/`isLoading`/`onLoadMore` from the `useAdminInfiniteQuery` result (`hasNextPage`, `fetchNextPage`).
- Exception — select vs search by cardinality: a `<Select>` is only for bounded sets that can be loaded in full (event types, sources, statuses — enumerations owned by the backend). Unbounded identity lists (session IDs, command IDs, actor IDs) get no picker at all until the backend offers a server-side search filter; do not fake discoverability with a paginated dropdown or a free-text "exact ID" input. Scope the view to the current/active entity instead (e.g. the events timeline tracks the current session).

## Filter bars

History/audit filter bars (`HistoryFiltersBar` and anything modeled on it) follow these rules so controls stay distinguishable and scannable:

- **One active state per group.** Mutually exclusive quick choices (time ranges) render as a segmented control — joined buttons, `aria-pressed`, exactly one highlighted segment (`bg-app-action`). Never a row of identical ghost buttons with no selected state.
- **The highlight reflects the query, not the last click.** Derive the active segment from the current filter values; when a range is set but matches no preset, show a neutral "Custom range" indicator instead of leaving a preset falsely lit.
- **Show what is applied.** The header carries an `N active` badge (narrowing filters only — `limit` is a page-size knob, never counted) and the reset button becomes `Reset (N)`.
- **Progressive disclosure.** High-traffic filters stay visible; rare ones (exchange, connector, environment, free-text IDs, limit) collapse behind a "More filters" toggle that auto-opens when one of them is set and shows a count badge while collapsed.
- **Labels are operator-facing.** No raw-value parentheticals (`Succeeded (succeeded)`, `Actor ID (actor_id)`) in labels or options; the raw value lives in the URL query, not the UI.
- **Hit areas.** Filter-bar buttons are at least `h-7`–`h-8` (28–32px); mode switches inside a label row are icon buttons (`aria-label` + `title`), not tiny underlined text links.

## URL-backed tab state

Page-level tab navigation must be reflected in the URL search param (`?tab=<id>`) so views are refresh-safe, shareable and survive history navigation. Do not keep the active tab in local state only.

- Register the param on the route via `validateSearch` (`src/app/router.tsx`) with an explicit union of tab ids; unknown values fall back to the default tab.
- The page derives the tab from `useSearch({ strict: false })` (validated → default fallback) and keeps a local mirror synced by `useEffect` so URL changes (back/forward, shared links) update the view.
- Every tab switch (button click or programmatic jump such as an audit lookup) calls `navigate({ to: '<route>', search: { tab } })`.
- Apply uniformly: `/activity` (`reconciliation` | `events` | `commands` | `audit`) and `/admin` (`connections` | `recovery` | `accounts` | `audit`) follow this pattern; new tabbed pages must follow it too.

## CTA availability and lock reasons

Every visible CTA must either work or explain itself. A disabled control whose
reason is hidden behind the disabled state is a defect, not a styling choice.

- A CTA rendered as enabled must perform the action it represents when clicked.
- When an action is locked, the lock reason must be reachable without first
  clicking an enabled control. Two accepted patterns, in order of preference:
  1. **Keep the entry control clickable.** The button that opens a form or
     dialog stays enabled; the confirm/submit action inside carries the lock
     and shows the reason there (warning callout at the top of the form).
  2. **Inline lock hint.** Show the reason in a `role="status"` line next to
     the disabled control, so the operator sees what to resolve without any
     interaction.
- Tooltips on disabled controls are not an acceptable reason carrier:
  disabled controls do not reliably receive hover, pointer or focus events.
- No silent omission. If an action is unavailable for a whole slot or section
  (for example the backend advertises no input fields, or the source is
  deployment-managed), render an explanatory line where the CTA would be —
  never render nothing.
- Lock reasons come from backend decisions (`decisions.<action>.detail` /
  `reasonCode` or an equivalent contract field). Display that text verbatim;
  do not invent reasons client-side.

Reference implementation: `ManagedCredentialsView` (Set credentials form).

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

## Feedback placement and lifetime

Follow [System feedback](FEEDBACK.md) when choosing inline errors, transient
toasts or persistent banners. Keep unresolved conditions visible and avoid
repeated announcements from polling.
