# Testing and quality gates

## Temporary dashboard E2E policy

While dashboard UI/UX is still evolving, do not write new E2E tests or expand existing E2E coverage as part of feature implementation. Defer this work until UI/UX is stable or the user explicitly requests it: repeated test rewrites for changing UI/UX provide little value during this phase. Continue applicable unit/component tests, lint, typecheck and build checks. Existing E2E tests and CI gates remain in place.

This is a **frontend starter**, not an integration with Life Vesting. The MSW fixture
is an example endpoint only; no external API, wallet or smart contract is required.

## Commands

| Command | Purpose |
| --- | --- |
| `pnpm run lint` | ESLint 9 flat config, TypeScript, hooks and JSX accessibility rules; zero-warning gate |
| `pnpm run lint:fix` | Apply ESLint's safe automatic fixes |
| `pnpm run typecheck` | TypeScript (strict) |
| `pnpm run test` | Watch-mode Vitest unit/component tests |
| `pnpm run test:run` | One-time unit/component tests |
| `pnpm run test:coverage` | Vitest V8 code coverage and enforced thresholds |
| `pnpm run test:e2e` | Playwright Chromium behavior and accessibility tests (non-visual) |
| `pnpm run test:a11y` | Playwright axe-core/keyboard-only tests |
| `pnpm run test:visual:update` | Capture and update baseline PNGs on a Linux/Chromium environment |
| `pnpm run test:visual` | Compare current screenshots against committed PNG baselines |
| `pnpm run check` | Lint + typecheck + coverage + build |

## MSW (Mock Service Worker)

`src/test/mocks/handlers.ts` defines a harmless GET fixture at
`https://example.test/api/health`. `src/test/mocks/server.ts` wires MSW for Node
and `src/test/setup.ts` starts it automatically for all Vitest tests, resetting
per-test overrides afterwards. Unhandled network requests are treated as errors.

Use `server.use(http.get(...))` inside the current test when customizing a response.
Do not use production secrets or a live backend to test UI. A feature can keep its
mock handlers within its own folder and share them with `src/test/mocks/handlers.ts`.

## Coverage

The initial thresholds enforced against **shared components, shared utilities and
the theme provider** are:

- Statements 75%; branches 65%; functions 75%; lines 75%.

The scope is deliberately explicit (`vite.config.ts`) so newly created feature code
is not counted as "covered" without tests. Add features to the include list or
configure stricter feature-specific thresholds as the codebase grows. Coverage
report: `coverage/index.html` after running `pnpm run test:coverage`.

## Accessibility

`e2e/accessibility.spec.ts` checks WCAG 2.0/2.1 A+AA structural rules with axe,
and verifies the dialog can be operated and dismissed with a keyboard and restores
focus. **The color-contrast axe rule is temporarily disabled**: colors inherited
from the LifeAI theme (e.g. light primary/success/danger text on white) may not
meet WCAG AA contrast ratios. This exception preserves the requested visual design
and should be removed only after design review and color-token remediation.

Use `pnpm run test:a11y` to reproduce the currently enforced checks. This does **not**
constitute a complete WCAG compliance certification. Preserve accessible names,
focus indication, keyboard operation and semantic controls when adding features.

## Visual regression

`e2e/visual.spec.ts` captures full-page screenshots of light Home and light/dark
UI kit, using a fixed Chromium viewport, reduced motion and disabled animations.

**Baseline bootstrap (one-time):** On Linux with Node 22 and Chromium installed:

```bash
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm run test:visual:update
# Review PNGs under e2e/visual.spec.ts-snapshots/ and commit them.
pnpm run test:visual
```

Screenshots generated on macOS or Windows may differ from Linux CI because of font
rendering. Prefer generating and updating baselines on Linux using the same Chromium
version as CI. Avoid updating a snapshot without reviewing the UI diff.

**Important:** Reviewed PNG baseline files are not bundled. To avoid false green approvals,
`pnpm run test:visual` requires baselines and will fail without them. The regular
`pnpm run test:e2e` excludes `@visual` tests. In GitHub Actions the visual job starts
running automatically once baseline PNGs are committed; until then, visual checks
are not enforced in CI. This is an explicit remaining setup step, not a passed test.

## Test conventions

1. Keep `*.test.ts(x)` near the unit/component under test; reserve `e2e/` for user journeys.
2. Test behavior from the user's perspective (`getByRole`, visible text), not private state.
3. Check accessibility for interactive elements: names, focus, keyboard and error states.
4. Mock HTTP with MSW; never make assertions based on live external API availability.
5. Reset mocks, timers, localStorage and document mutations between tests when necessary.
6. Add regression coverage for every bug fix and new shared UI primitive.
7. Keep screenshot baselines reviewed and versioned; don't auto-approve visual diffs.
8. The CI gate rejects lint warnings, TS errors, failing tests, coverage regressions and E2E failures.

## CI

`../../.github/workflows/frontend.yml` runs lint, typecheck, coverage, build and Chromium E2E
(including structural accessibility) from `dashboard/`. It runs for dashboard changes
on PRs and pushes to `dev`, `main`, `master`, `development` and the existing
`refactor/unit_tests**` / `epic/**` branch families; manual dispatch is also available.
Changes to that workflow also trigger it. Successful builds upload `dashboard-dist`
containing `dashboard/dist/`. This artifact is separate from backend Docker images.
Visual snapshots run if snapshot PNGs have been committed. Installs use `pnpm-lock.yaml` with
`pnpm install --frozen-lockfile`; update the lockfile with `pnpm install` when changing dependencies.
