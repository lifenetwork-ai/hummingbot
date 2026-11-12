# Dashboard UI and logic integration

Acceptance against every TODO criterion is tracked in [ACCEPTANCE-AUDIT.md](ACCEPTANCE-AUDIT.md). This integration record establishes the tested paths, not completion of all TODO criteria.

## Scope and ownership

The operational routes use the frontend controllers documented in [LOGIC-INTERFACES.md](LOGIC-INTERFACES.md). Presentation remains in the existing LifeAI components and layouts. The integration owns callback/data binding, capability decisions, operation tracking, financial semantics and the corresponding tests. It changes no backend endpoint, generated DTO or deployment configuration.

The local checkout is branch `dev`, with unrelated UI edits retained. The imported logic was compared with its input snapshot before applying the logic-only patch. Integration is uncommitted; this document does not certify a deployed revision.

## Case matrix

| Workflow | Integration evidence |
|---|---|
| Auth/workspace | Public routes avoid operational queries; restoration verifies before workspace mount; logout/relogin removes old cache and polling; receipt scope follows API/principal/bot. |
| Overview/assets | Shared status/balance sources; liquidity/positions independent; stale/session-mismatch retained; missing account availability does not fall back to equity/cash; multiple accounts remain separate; absent positions/policy do not imply Flat or safe. |
| Session | UI reviews Start against catalogue, sends the reviewed version/hash, and follows the exact receipt. Until-stopped sends null duration. A timeout restores a candidate after remount without replay and retries the same body/key. Viewer cannot submit. Pinned configuration is distinct from current catalogue. |
| Stop | Independent controller lane refreshes current session/version; global dialog surfaces unknown/error without reporting completion from acceptance; positions remain open. Existing controller tests cover Stop while Switch is pending. |
| Connections | UI uses backend input names and slot/control versions, clears secret inputs on success/failure, and retries unknown operations only after explicit same-key review with re-entered credentials. Root cleanup completion requires all four proofs; partial cleanup/retry/repair remain independently observable. |
| Recovery | UI checks and polls the exact operation, displays server expiry/blockers/proposed changes, and confirms the returned preview token/version. The token is absent from rendered text/storage. Reset success requires recovered, and no Start is sent. Tests also cover timeout retry, permission loss, definitive rejection and late responses after scope change. |
| Accounts/audit | Permission gates are independent; account changes carry the row version. Last-bound-Admin conflict is displayed unchanged; invitation differs from bound login. Audit remains available with read permission and has cursor loading. |
| Orders/history | Server-bound cursor filters, on-demand selected order/fills, independent detail/fill errors, and scope/ordering tests. Recorded open orders are explicitly not a live exchange resting snapshot. |
| Reconciliation | Exact check receipt remains distinct from latest report; succeeded/mismatch remains mismatch. Bounds, permission, cooldown and unchanged retry are enforced. |
| Reporting | Original timezone/timestamps and stale retry hint; invalid ranges issue no new request. Exact decimal addition preserves large/tiny amounts. Missing day/funding is a gap/null; fees retain currencies and paid/rebates; conversion coverage comes from counts rather than a fixed 100%. Numeric chart geometry does not replace decimal labels. |
| Existing browser cases | Updated selectors follow the current account/theme menus and context drawer. Same cases retain auth, reload, no-replay, receipt meaning, keyboard and 320px overflow assertions. No new E2E workflow suite is added while UI is evolving. |

The failure matrix reuses the API/auth tests for transient/persistent dependency failures, retries, request sharing, cache replacement, ordering and session changes. Feature and page tests add the bindings above. Two real operators, backend restart and exchange effects are not proven by mocks.

## Runtime and external boundaries

Local runtime is Node 26.8.2 with pnpm 12.4.1; CI's Node 22 gate remains separately unverified. Playwright supplies synthetic Google credentials, pins `VITE_ADMIN_API_BASE_URL=/api/v1/admin`, mocks mounted operational reads, and starts its own Vite server. The developer `.env` and its backend URL do not determine browser test scope.

Google authorized origins/whitelist, gateway/CORS/IAM, actual Demo/Live account identity, hosted Pages deployment/rollback, real exchange cancel/close/reconciliation and backend restart are **SKIP** here. Hosting and real-system acceptance are outside the current T-01–35 frontend backlog. No production mutation or deployment is performed.

Structural axe checks retain the repository's color-contrast exclusion. A passing a11y command is not a complete contrast or WCAG certification. Reviewed visual baselines remain required before visual regression can pass.

## Reproduction

Run from `dashboard/`:

```bash
pnpm run api:check
pnpm run check
pnpm run test:e2e
pnpm run test:a11y
```

If another process is writing the default Vitest coverage temporary directory, serialize those checks or use an isolated report directory without changing coverage thresholds:

```bash
pnpm run lint
pnpm run typecheck
node node_modules/vitest/vitest.mjs run --coverage --coverage.reportsDirectory=/private/tmp/hummingbot-integration-coverage --reporter=verbose
pnpm run build
```

Verbose browser reruns preserving selectors and timeout:

```bash
pnpm exec playwright test --grep-invert @visual --reporter=list --timeout=30000
pnpm exec playwright test e2e/accessibility.spec.ts --reporter=list --timeout=30000
```

Visual: `pnpm run test:visual` is **SKIP** until reviewed baselines exist. A dependency/permission skip is never counted as a pass.

## Local results

Validation record (2026-11-11, `dev`, base HEAD `4b4188e1d` plus uncommitted changes):

| Result | Command | Evidence |
|---|---|---|
| PASS | `pnpm run api:check` | Generated TypeScript matches checked-in OpenAPI. |
| PASS | `pnpm run check` | Lint and strict typecheck; 81 files / 396 tests; coverage thresholds unchanged; production build completed. Local execution required permission for the theme generator's tsx IPC socket. |
| PASS | `pnpm run test:e2e` | 30 existing Chromium cases passed, including auth/new-tab/reload, receipt inspection/no replay and 320px workspace checks. Polling removal is observed beyond the current 15-second status cadence. |
| PASS | `pnpm run test:a11y` | 4 structural/keyboard cases passed, with the existing contrast exclusion unchanged. |
| PASS | `git diff --check` | No whitespace errors in the tracked diff. |
| SKIP | `pnpm run test:visual` | Reviewed visual baselines are unavailable. No baselines were generated or approved. |
| SKIP | Node 22 / hosted and real-system acceptance | Local evidence uses Node 26.8.2 and mocked API/Google; hosting and real-system acceptance are excluded from this backlog. |

Coverage: statements/lines 90.91%, branches 81.02%, functions 96.53%. The Vite build reports a 776.23 kB JavaScript chunk (222.99 kB gzip); bundle splitting/performance tuning is not part of this logic integration. No dependency, backend schema or production deployment was changed.
