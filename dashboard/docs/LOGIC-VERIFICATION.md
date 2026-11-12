# Isolated dashboard logic verification

This record describes the logic-only snapshot before UI integration. Current branch integration evidence is recorded in [INTEGRATION-VERIFICATION.md](INTEGRATION-VERIFICATION.md).

## Scope

This matrix covers frontend logic for T-05–31 and the logic portion of T-32–35. The original T-01–04 auth/context/API tests remain part of the regression suite. Generated DTOs are checked against the checked-in OpenAPI artifact; this does not regenerate or verify a live backend schema.

UI composition, new browser workflows, responsive/keyboard/contrast review and visual baselines belong to the integration/UI gates. Hosted Pages, Google/gateway/backend/exchange acceptance and backend restart effects remain T-36–37. No live trading or production mutation is used by these tests.

## Case matrix

| Case family | Existing evidence reused | Added logic evidence | Boundary |
|---|---|---|---|
| Success and completion meaning | API adapters, receipt inspection/invalidation | Overview mappings; command and reconciliation receipts; four credential cleanup proofs; recovery check/review/reset; account/version payloads; reporting series | Receipt success remains distinct from observed business outcome. |
| Validation and permissions | Auth 401/403, API field errors, permissions/context | Catalogue/Duration review, action availability, bounded reconciliation, server credential bundle names, recovery eligibility/expiry, account validation and audit permission, report date range | Backend still validates every POST. |
| Transient dependency failure | `core/api/hooks.test.tsx`, `queries.test.ts`, `client.test.ts` retry/backoff | Command/check timeout same-key retries, credential timeout without secret persistence, independent report sources | Mock HTTP only. |
| Persistent failure and recovery | Operational context last-good/error and auth tests | Independent liquidity/positions/report sources, stale observation mapping, definitive-error review path | Last-good data is not current authority. |
| Retry and unknown outcome | Typed mutations never automatically retry | Scoped journal candidates, unchanged command/reconciliation/check requests, separate Stop, credential retry/repair versions | Credential secrets and recovery Reset bodies are not persisted. |
| Duplicates and idempotency | In-flight API mutation guard and accepted receipt scope tests | Journal channel isolation, same-key equality and stale-response deletion guard; command receipt lifecycle; credential decision locks | Browser storage is not a distributed lock. |
| Response ordering | Session cache replacement/cancellation and cursor tests | Filter-specific history/report queries, per-exchange credential results, scope guards on control operations | No proof of real concurrent exchange effects. |
| Concurrency | API duplicate/captured scope tests | Stop during pending Switch; independent Stop/session/reconciliation journal channels; permission changes | Two live operators remain a backend/integration test. |
| Reload/restart | Auth persistence and receipt reference tests | Journal restoration without replay, recovery receipt references, root credential IDs supplied by status | Backend restart and venue recovery are SKIP in local mocks. |
| Independent side effects | Receipt invalidation and query lifecycle tests | Reconciliation mismatch after receipt success, cleanup four-proof completion, Recovery recovered without Start, status/report independence | Real cancel/close/ledger effects are SKIP. |
| Financial meaning | DTO exact values | Tiny/large decimal strings, nullable funding, missing-day gaps, shared balances once, separate policy/position sources | Chart numbers are presentation only; no new financial aggregation or fabricated series. |

The matrix describes bounded assertions, not exhaustive combinations of every feature and failure. Missing eligible rolling coverage/restriction history remains a contract limitation; T-19's owner/version match is represented by the server-connected assertion. T-07 per-order source contract is added but backend tests and Swagger regeneration are pending in a provisioned Python environment.

## Commands

Run from `dashboard/` after `pnpm install --frozen-lockfile`. Node 22 is the CI target; the local validation record below uses Node 26.8.2. The direct Node commands invoke the same tools as the package scripts while avoiding concurrent package-manager dependency reconstruction.

```bash
node --import tsx scripts/generate-api.ts --check
node node_modules/eslint/bin/eslint.js . --max-warnings 0
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vitest/vitest.mjs run --coverage --reporter=verbose
node --import tsx scripts/generate-theme.ts
node node_modules/vite/bin/vite.js build
```

The combined project gate remains `pnpm run check`. A full test failure cannot be reported as a passing project gate even when a bounded regression selection passes.

## Browser and external reruns

Existing browser checks require a local Vite server and Chromium. New E2E authoring is deferred by the temporary policy in [TESTING.md](TESTING.md). The configured server supplies the synthetic Google client ID; browser tests use HTTP mocks, not Google/exchange production credentials.

```bash
pnpm exec playwright test --grep-invert @visual --reporter=list --timeout=30000
pnpm exec playwright test e2e/accessibility.spec.ts --reporter=list --timeout=30000
```

Visual comparison is SKIP until reviewed baselines are available. Node 22 validation, new integrated workflow browser tests, real Google/gateway/CORS/IAM, live/demo venue effects and hosted deploy/rollback are SKIP in this logic-only validation. They require their own authorized environment and T-33–37 evidence; no single local unit-test command can establish them.

A dependency fetch failure is SKIP for checks it prevents, never PASS. Recreate dependencies with `pnpm install --frozen-lockfile` after registry DNS is available, then rerun the exact selected command above. Local browser validation may require permission for Vite/tsx sockets.

## Validation record

The current local result table is maintained with the final verification of this logic handoff. It preserves pre-existing UI assertion failures separately from logic regressions.

Local record: 2026-11-11, Node 26.8.2, pnpm 12.4.1. Dependency and devDependency resolutions in the available dependency snapshot match the lockfile importer. A clean registry installation was not established: offline tarballs were missing and online registry DNS failed. This is distinct from the direct-tool results below.

| Result | Check / exact command | Evidence |
|---|---|---|
| PASS | `node --import tsx scripts/generate-api.ts --check` | Checked-in generated schema matches checked-in OpenAPI. |
| PASS | `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | No lint errors or warnings. |
| PASS | `node node_modules/typescript/bin/tsc --noEmit` | No TypeScript diagnostics. |
| FAIL | `node node_modules/vitest/vitest.mjs run` | 309 passed, 3 failed, 57 files. The same 3 failures existed at the input baseline: one `AuthenticatedWorkspace.test.tsx` and two `SessionRestoring.test.tsx` English-copy assertions against Vietnamese UI. UI/test copy is outside the logic ownership. |
| PASS | `node node_modules/vitest/vitest.mjs run --coverage --exclude src/app/AuthenticatedWorkspace.test.tsx --exclude src/features/auth/SessionRestoring.test.tsx` | 307 tests / 55 files PASS. The two excluded UI files contain 5 tests (3 baseline failures and 2 passing tests); none is counted in this selection. The baseline failures remain FAIL above. Coverage includes every new logic feature: statements/lines 96.41%, branches 85.61%, functions 97%; thresholds are unchanged. |
| PASS | `node --import tsx scripts/generate-theme.ts` and `node node_modules/vite/bin/vite.js build` | Production bundle generated. Vite retains the existing >500 kB chunk-size warning. |
| PASS | Snapshot path/content audit and `git apply --check` on the isolated logic patch | Only logic, tests, handoff documentation and coverage configuration differ from the input snapshot; UI/page/shell/theme/E2E files are unchanged. |
| FAIL | `pnpm install --offline --frozen-lockfile`; `pnpm install --frozen-lockfile` | Missing cached tarballs, then registry DNS failures. Validation used an isolated available dependency snapshot, not a proven clean install. Rerun online installation followed by the selected commands when registry access is available. |
| SKIP | `pnpm exec playwright test --grep-invert @visual --reporter=list --timeout=30000` | Local server could not start: sandbox denied the tsx IPC socket. Escalated execution was not run because automatic approval review timed out, then failed with 502. |
| SKIP | `pnpm exec playwright test e2e/accessibility.spec.ts --reporter=list --timeout=30000` | Same required local-server permission boundary; no new browser/a11y result is claimed. |
| SKIP | `pnpm run test:visual` | Reviewed visual baselines are unavailable; UI unchanged. |
| SKIP | Node 22 CI, new integrated T-33–34 browser journeys, hosted T-36 and real-system T-37 | Not established by local Node 26 and mock observations. |

For an expanded verbose rerun of the bounded regression selection, preserve both exclusions:

```bash
node node_modules/vitest/vitest.mjs run --coverage --reporter=verbose \
  --exclude src/app/AuthenticatedWorkspace.test.tsx \
  --exclude src/features/auth/SessionRestoring.test.tsx
```

Rerun the baseline failures without exclusions using `node node_modules/vitest/vitest.mjs run src/app/AuthenticatedWorkspace.test.tsx src/features/auth/SessionRestoring.test.tsx --reporter=verbose`. Their UI-owner resolution is required before the full project gate can pass.
