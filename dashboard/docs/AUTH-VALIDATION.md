# Authentication validation

2026-11-10 — branch `dev`, dashboard source only. No backend or deployment changes.
Runtime: Node 26.8.2 in this environment; the project recommends Node 22 LTS.
Node 26's experimental global web storage conflicts with jsdom, so Vitest commands
below disable it rather than replacing browser storage with a fake.

## Case matrix

| Workflow | Evidence | Result |
| --- | --- | --- |
| Login, header-only token, current identity, cookie omission | AuthProvider unit tests | PASS |
| Rejected/expired credentials and persistent denial | AuthProvider unit tests; denied browser login | PASS |
| Transient identity lookup failure, explicit retry, no automatic replay | AuthProvider unit tests | PASS |
| Concurrent duplicate login; logout while login pending | AuthProvider unit tests | PASS |
| Old request 401 arriving after a new login | AuthProvider unit tests | PASS |
| Active request 401, JWT expiry, disabled account | AuthProvider unit tests | PASS |
| 403 refreshes current permissions and replaces cache identity | AuthProvider unit tests; existing API session isolation tests | PASS |
| Protected deep link, safe return URL, logout and reload | redirect unit tests; Playwright login journey | PASS |
| Missing Google configuration, SDK failure/retry, ignored late callbacks | GoogleSignIn unit tests | PASS |
| SDK single load, network failure and bounded timeout | google-script unit tests | PASS |
| Login mobile layout, keyboard, light/dark structural accessibility | Playwright login/accessibility tests | PASS |
| Live Google signature/audience, whitelist, CORS, gateway and IAM | No deployed environment or credentials exercised | SKIP |
| Screenshot regression baseline comparison | No reviewed baselines installed | SKIP |

Browser Google credentials and responses are synthetic mocks. Behavioral browser
checks stub external fonts; screenshot review validates composition, not Google's
real button rendering. Login screenshots were reviewed at desktop light and 320px
dark. Structural axe checks preserve the existing color-contrast exclusion.
No refresh endpoint exists; reauthentication is the supported recovery. There are
no exchange mutations, database changes or trading side effects in this feature.

## Commands and boundaries

Run from `dashboard/`:

| Command | Result | Evidence |
| --- | --- | --- |
| `pnpm run skills:check` | PASS | 10 skill definitions |
| `pnpm run lint` | PASS | Zero errors/warnings |
| `pnpm run typecheck` | PASS | Strict TypeScript |
| `pnpm run build` | PASS | Vite production build |
| `NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run --coverage --exclude src/core/api/admin.test.ts` | PASS | 95 tests; coverage gates pass, auth included |
| `NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run --coverage` | FAIL | 1 existing operation-map failure; 100 tests pass |
| `pnpm run api:check` | FAIL | Existing generated schema types stale |
| `pnpm exec playwright test --grep-invert @visual --workers=2` | PASS | 13 tests: login, navigation, keyboard and accessibility |
| `git diff --check` (repository root) | PASS | No whitespace errors |
| `pnpm run test:visual` | SKIP | No reviewed screenshot baselines |

Both API failures predate this feature: comparison against `HEAD` confirms the
existing adapter lacks `checkRecovery`, `getRecovery`, `getRecoveryOperation` and
`resetRecovery`. Neither API schema nor generated types nor adapter operation map
is changed by this feature. Excluding that test is a diagnostic boundary, not a
claim that the full suite passes.

Verbose browser rerun (30-second per-test timeout):

```sh
pnpm exec playwright test e2e/login.spec.ts e2e/accessibility.spec.ts --workers=2 --reporter=list --timeout=30000
```

Visual baseline comparison after separately reviewing/committing Linux baselines:

```sh
VISUAL_BASELINES_READY=1 pnpm exec playwright test e2e/visual.spec.ts --reporter=list --timeout=30000
```

Live acceptance requires the actual OAuth client ID, authorized JavaScript origin,
approved account and reachable backend/gateway. Configure `.env.local` as described
in API.md, run `pnpm run dev`, then verify real login, expiration and reconnect.
No automated live-acceptance test exists; mocked test reruns cannot qualify it.

## Authentication layer refactor

Session context/provider and colocated tests live in `src/core/auth/`.
Route guard, return-path validation and its tests live in `src/app/`.
`src/features/auth/` contains only Google sign-in UI and the SDK loader/tests.
Existing behavior was locked by 24 auth/routing tests before moving files; the
same 24 tests pass afterwards. No fallback branches or authentication behavior
were changed. A separate read-only import review found no `core/auth` imports
from `features`, `pages` or `app`, and no obsolete auth-module paths.

Post-refactor results: lint, typecheck, production build and whitespace checks
PASS; 95 unit tests with the previously documented operation-map test excluded
PASS with coverage gates; 13 browser tests PASS. Live acceptance and visual
baseline boundaries above still apply.

Starter-origin check: the current `tiendn/life-ai-starter` main tree was inspected
through the GitHub API. `src/core/` and `src/features/` each contain only `.gitkeep`;
there is no upstream authentication context/provider. The misplaced context was
introduced by this dashboard's login implementation, so no starter-kit PR is
required for this issue.

## Public UI Kit route validation — 2026-11-10

The current route contract supersedes the earlier protected-UI-Kit journey:
`/ui-kit` and `/login` are public; `/` remains protected. Validation below applies
only to the local `dev` dashboard under Node 26.8.2 and mocked Chromium auth.
It does not establish deployed gateway behavior or live Google/backend acceptance.

| Case | Existing coverage and update | Result |
| --- | --- | --- |
| Anonymous direct UI Kit access and reload | Added login E2E case; asserts no login/me API requests or signed-in controls | PASS |
| Protected home with query/hash return destination | Moved protected deep-link journey to `/?market=spot#orders` | PASS |
| Logout/reload clear protected access | Preserved home login journey and token-storage assertion | PASS |
| Public UI Kit to protected home | Brand keyboard navigation checks login gate, then successful home entry | PASS |
| Denied login, explicit retry, unsafe return URL | Existing login cases retained | PASS |
| Dialog keyboard/focus, structural axe, themes and narrow layouts | Removed obsolete UI Kit sign-in steps | PASS |
| Feedback demos on public route | Removed conditional login fallback; public route is asserted explicitly | PASS |
| Auth expiry, concurrent/duplicate login, stale requests and independent session effects | Existing AuthProvider/API session tests unchanged and passing in full unit run | PASS |
| Visual baseline comparison | Test entry updated; no reviewed PNG baselines exist | SKIP |
| Live external auth and deployed routing | Not exercised by local mocked tests | SKIP |

Commands run from `dashboard/`:

| Command | Result | Evidence |
| --- | --- | --- |
| `pnpm exec playwright test e2e/login.spec.ts --grep 'protects deep links' --workers=1 --reporter=list --timeout=10000` before test changes | FAIL | Reproduced obsolete expectation: `/ui-kit` stays public instead of redirecting |
| `pnpm exec playwright test --grep-invert @visual --workers=2 --reporter=list --timeout=30000` after changes | PASS | 21 tests, including structural accessibility and feedback demos |
| `pnpm run lint` | PASS | No errors/warnings |
| `pnpm run typecheck` | PASS | Strict TypeScript |
| `node --import tsx scripts/generate-theme.ts && pnpm exec tsc --noEmit && pnpm exec vite build` | PASS | Production build; direct Node loader avoids sandbox-restricted tsx IPC |
| `node scripts/validate-skills.mjs` | PASS | 10 skills and local links |
| `git diff --check` | PASS | No whitespace errors |
| `NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run --coverage` | FAIL | 132 passed, 1 failed: unchanged admin operation-map test lacks four recovery operations |

Diagnostic coverage run: `NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run --coverage --exclude src/core/api/admin.test.ts` — PASS, 127 tests; coverage gates pass (98.26% lines, 87.42% branches). This excludes the entire six-test adapter file and does not establish a passing full suite.

The full unit suite remains failing; no tests or coverage thresholds were weakened.
`src/core/api/admin.ts` and `admin.test.ts` have no changes against HEAD. The failure
matches the existing API boundary documented above and is separate from public routing.
Chromium required sandbox escalation to start its local Vite server; it subsequently ran.
The structural axe checks retain the documented color-contrast exclusion.

Visual rerun after separately reviewing/committing Linux baselines:

```sh
VISUAL_BASELINES_READY=1 pnpm exec playwright test e2e/visual.spec.ts --workers=2 --reporter=list --timeout=30000
```

Live acceptance prerequisites and procedure remain those described above;
there is no automated live external-auth test to rerun.
