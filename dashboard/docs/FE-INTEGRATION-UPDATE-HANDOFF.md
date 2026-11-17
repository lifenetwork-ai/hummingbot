# FE integration update: logic and UI contract

## Scope and ownership

Source of truth: `../../docs/fe-integration.md` and `../../docs/api/openapi.json`.
Target checkout: repository `dev`. No backend, deployment, credential or trading changes.

Logic owns generated schema, pure mapping, controllers, validation and their tests.
UI owns presentation TSX, component tests, page composition and page tests. Preserve
other contributors' edits; do not commit or push during this handoff.

The integrated UI uses the interfaces below. Local integration evidence is recorded
in Integrated verification; hosted/operator acceptance remains separate. No new/expanded E2E coverage while UI
is evolving, per `../AGENTS.md`.

## Controller interfaces

### Overview

`useOverviewController(shared)` retains its existing return shape and adds:

- `recoveryPresentation`: return type of `deriveRecoveryPresentation` in
  `src/features/recovery/presentation.ts`.
- `diagnostics`: `OperationalDiagnostics` from `src/features/overview/diagnostics.ts`.
- `processDiagnostics`: process-only HTTP 503 diagnostics or null. Includes
  `phase`, `reasonCode`, `observedAt`, `requestId`, `observation='process'` and
  `persistedStateAvailable=false`; never supplies an owner/version/session.
- `overviewSupplement`: the generated `OverviewSupplementOutput` from
  `status.overview`, accepted only with fresh owner evidence and matching observed
  session identity. Missing reporting stays null and does not hide independent
  status, balances, positions, liquidity or Stop controls.

Overview reporting uses the generated OpenAPI types. Rolling target qualification
requires a fresh complete observation, `measurement_ready=true`, a full window,
positive eligible time and `conservative_eligible_fraction`; total-time coverage
has a separate denominator. Eligibility uncertainty and restrictions stay visible.
Fee catch-up state and history completeness are independent: `ready` does not
certify complete history, and `catching_up` does not qualify a cost verdict.

### Recovery

`useRecovery()` adds `presentation` and `verification`.

`presentation` fields:

| Field | Meaning |
| --- | --- |
| `observation` | `fresh`, `stale`, `unknown`; requires current owner/session evidence and automatic DTO not stale |
| `automatic` | Raw DTO or null; its numeric timestamps remain epoch seconds |
| `phase`, `active` | Active automatic work is reconciling/bootstrap/waiting for readiness, with fresh evidence |
| `showManualHalt` | HALT except when backend next action is wait |
| `waitingForShutdown` | HALT with next action wait; use returned operator guidance |
| `showNoRecoveryNeeded` | Fresh explicit non-HALT and automatic absent/idle; never infer from absent status |
| `retryAt` | Future epoch milliseconds only during active fresh automatic work; null otherwise |
| `startedAt`, `lastProgressAt` | Epoch milliseconds or null |
| `isolated`, `admissionMode` | Scoped uncertainty; funded Spot SELL only does not mean two-sided liquidity |
| `scopedSellOnly`, `tradingStopped` | Fresh active running scoped admission versus retained uncertainty after Stop |
| `quarantinedOrders`, `reservedBase`, `reservedQuoteWorstCase` | Recorded uncertainty; decimal amounts remain strings |
| `operatorRequired`, `reasonCode`, `orderId`, `coldRecoverySupported`, `halted` | Server context; none grants permissions |

Only interpret admission/reservation fields as current when observation is fresh.
Stale metadata may remain visible with its stale label. A stopped reserved obligation
is not canceled or erased. `manual_required` does not display an automatic retry countdown.

`verification` fields: `scope`, `checkpointPreserved`, `ordersChecked`, `fillsChecked`,
`ordersCheckedMeaning`, `proposedChanges`, `riskStopBlockers`.
`ordersCheckedMeaning='no_historical_per_order_reads'` explains zero historical
order reads in verified-checkpoint scope. It does not mean the account was unchecked.
For `checkpointPreserved=true`, empty proposed changes are valid: financial rows,
remaining basis and risk checkpoints stay intact. Reset establishes a new verified
anchor and clears the technical HALT; Start remains separate.

Existing `checkDecision`, `reviewDecision`, receipt, retry and confirmation-token
handling remain authoritative. No new action is introduced by these view models.

### Diagnostics

`diagnostics.reductions[]` has `market`, `observedAt`, `observation`, `evidence`.
The evidence is the typed `inventory_reduction` DTO, preserving fee readiness,
mode, price mode and BUY/SELL side fields including OPEN/CLOSE and target hedge leg.
Five-second expiry applies specifically to reduction evidence; stale/future/missing
times or unverified owner context cannot prove current reduction capacity.

`diagnostics.outflow` has `observation`, `pendingTerminalOrders` (number or null),
`waitingForTerminalVerification`, `evidence`. A fresh positive pending count means
a BUY-only verification wait even without an incident; zero/missing count does not
prove the account is reconciled or the spending ceiling is exhausted. Outflow uses
backend freshness rather than an invented five-second TTL.

`diagnostics.terminalIncidents[]` preserves recognized incident fields and adds
`terminalStage`: order_read/order_terminal/order_identity/fills_read/fill_identity/
fill_coverage/order_recheck/recording. Unknown stages remain in full service incidents.

`diagnostics.service` contains raw safe DTO `evidence` and `observation` including
`process` when applicable. Render service phase/incidents separately from working
quotes. On failed status reads retained account/service evidence is stale; use
`processDiagnostics` for current process-only 503 information.

Controller-local clocks reevaluate cached reduction expiry/recovery countdowns
every second while relevant evidence exists. They issue no network requests.
Components must not add competing HTTP polling or mutation retry schedules.

### Session duration

`useSessionControl().defaultDurationMode`, or pure
`defaultSessionDurationMode(profiles)`, resolves the deployment default. Until
stopped requires both advertised duration capability and catalogue allowance;
otherwise Timed is the compatibility fallback.

Initialize only an untouched form after profiles load. Preserve explicit user or
initialConfig choices across rerenders/refetches. Keep timed input drafts local;
reviewed Until stopped requests continue sending null duration seconds. The helper
does not rewrite pinned sessions or command receipts.

### Credential cleanup

Each `useConnectionsController().slots[].cleanup` retains its root identity and
adds `stage`, `label`, `complete`, plus existing blockedStage/reasonCode/nextRetryAt/
finalProofs. `cleanup_orders_pending` maps to **Waiting for order verification**.
`nextRetryAt` remains the API UTC ISO string. Only successful removed stage plus
all four proofs maps to **Disconnected**. `slot.disconnectedProof` additionally
retains the existing saved-slot state check.

Use `cleanup.label` for operator copy; show raw stage/reason in Details.
Configured credentials remain visible during uncertainty. Never add force delete.

## UI work and acceptance cases

1. Wire recovery presentation into Overview and Admin. Test active/operator-required,
   stale, idle, settled-stopped, manual-required and shutdown-wait states. Retain Stop
   and backend action availability. No misleading emergency/manual reset CTA on wait.
2. Wire default duration once; test Demo default, old-server fallback, explicit choice
   preservation, asynchronous profiles and hidden timed drafts.
3. Render service/incidents, perpetual reduction and outflow wait in progressive
   details. Test stale/future/expired reduction, missing evidence, zero and no incident.
4. Make recovery review/confirmation scope-aware, including valid empty changes,
   zero historical order reads and risk-stop guidance with Recover disabled.
5. Render cleanup label/retry and retained connection; test uncertain cleanup never
   becomes Disconnected from zero rows or a timeout.
6. Add `automatic_recovery_order_rejected` to supported activity audit filter options.

Use English product copy, existing semantic tokens, responsive light/dark layouts,
accessible focus/keyboard and existing feedback components. No new dependencies.

## Logic test matrix

| Concern | New/existing evidence |
| --- | --- |
| Success, missing/null/zero | presentation, diagnostics, duration-default, cleanup-progress tests |
| Validation/permissions/stale owner | session-control, recovery, connections and controller tests |
| Transient/persistent source errors | useOverviewController independent fail/recover tests; API hook tests |
| Retry/recovery and timeout unknown | useRecovery/useConnectionsController/useSessionControl lifecycle tests |
| Duplicates/idempotency | connection in-flight locks and original-key retry; session/restored receipt tests |
| Ordering/context switches | connection out-of-order exchange reads; recovery late receipt after scope change |
| Concurrent operators | existing control-version/catalogue validation and recovery preview gates; backend authority unchanged |
| Restart/reload | existing operation journal/receipt tracking/remount tests; no new persistence |
| Independent effects | cleanup requires every proof; recovery never auto-starts; reporting errors retain controls |
| Cached expiry/unmount | useObservationTime timer lifecycle; diagnostics five-second boundary tests |
| External account/backend correctness | Outside local MSW/JS scope; no live exchange, DB or deployed acceptance claimed |

## Integrated verification

Checkout: `dev`, base commit `a3f3ae49a`, local uncommitted integration.
Runtime: Node v26.8.2; repository recommends Node 22. On Node 26, unit tests use
`NODE_OPTIONS=--no-experimental-webstorage` to use jsdom storage instead of Node's
experimental Web Storage implementation. No authentication production code was
changed for this compatibility setting.

### Integration corrections

- Duration initializes after profiles arrive, preserving the deployment default and
  an operator's explicit selection.
- Overview receives process-only HTTP 503 diagnostics separately from retained
  stale account/service observations. A responding process is not labeled dead.
- Missing reduction evidence is unreported, not proof that a policy is disabled.
  Stale/unknown outflow zero counts are last-recorded values, not current clearance.
- Scoped recovery reports current funded-SELL-only admission only with fresh active
  running evidence. Stopped/stale uncertainty never grants trading permission.
- Explicit zero reserved quote is displayed as zero; absent values remain unknown.
- Shutdown wait blocks manual Check/Reset and their retries in UI and logic, even
  if a previous recovery overview still advertises availability.
- Admin uses the controller's recovery presentation. Settled-stopped reservations
  stay visible; scope descriptions do not claim failed checks matched the account.
- `status.recovery=null` deliberately prevents a no-recovery claim. `/recovery`
  is not substituted for missing runtime automatic-recovery evidence.
- AssetRibbon tests follow the current compact balance presentation; controller
  assertions retain flat/unknown coverage for complete, stale and missing evidence.
- Two existing starter E2E heading selectors now target Assigned mandate. Test
  count and navigation/login/reload/theme/keyboard assertions are unchanged.

### Case matrix

| Case | Evidence |
| --- | --- |
| Published reporting and independent failures | HomePage completion tests: portfolio from status, positions failure isolation, failed status retry, zero command writes |
| Reporting freshness and session isolation | Overview model tests: null, stale, status error and session mismatch |
| Eligible SLO qualification | Reporting mapping, LiquidityVerdictCard and decision tests: conservative denominator, unknown/restricted time, readiness, full window and positive eligible time |
| Fee catch-up and history completeness | Reporting and decision tests: exact decimal totals, retained stale values, independent catching-up and incomplete-history labels |
| Deployment credential ownership | Connections tests: Set/Disconnect/Retry/Repair blocked for deployment-managed catalogue or status |
| Success, null/missing/zero | Recovery/diagnostics/cleanup helpers and component tests |
| Deferred profiles and explicit duration | SessionConfigForm async initialization and existing SessionPage payload tests |
| Fresh/stale/unknown, timer expiry | Recovery presentation, reduction five-second boundary, observation clock tests |
| HTTP 503 and independent reporting failures | HomePage process-diagnostics regression and Overview controller source recovery tests |
| Shutdown wait and stale availability | Recovery decisions, Admin component and unknown-Check retry regression |
| Permission/version validation | Existing recovery/session/credential lifecycle tests |
| Transient/persistent failure, retry and unknown outcomes | Existing controller/API hook tests plus new shutdown retry guard |
| Duplicates/idempotency, ordering and context isolation | Existing connection locks, session journal/receipt and scope-change tests |
| Reload/restart | Existing receipt tracking/remount tests; no new persistence |
| Independent side effects | All cleanup proofs remain required; recovery never automatically submits Start |
| Concurrent operators | Existing version/catalogue/preview gates; frontend/MSW evidence only, not live backend concurrency |
| Flat versus unknown positions | AssetRibbon controller cases for complete owner observation, unavailable owner/coverage, stale and missing time |
| Browser navigation/theme/keyboard/accessibility | Existing Playwright suite; no new E2E coverage added |

### Commands and results

Local frontend checks on `dev`, Node 26.8.2 / pnpm 12.4.1. API checks compare
the generated frontend types with the checked-in OpenAPI artifact.

| Result | Command | Evidence |
| --- | --- | --- |
| PASS | `pnpm --dir dashboard run api:check` | Generated types match checked OpenAPI |
| PASS | `pnpm --dir dashboard run typecheck` | No TypeScript diagnostics |
| PASS | `pnpm --dir dashboard run lint` | No warnings/errors |
| PASS | `NODE_OPTIONS=--no-experimental-webstorage pnpm --dir dashboard run test:coverage` | 132 files, 735 tests; configured coverage thresholds passed |
| PASS | `pnpm --dir dashboard run build` | Standard production build; existing large-bundle warning |
| PASS | `git diff --check` | No whitespace errors |
| PASS | `pnpm --dir dashboard run test:e2e` | Existing suite: 30 cases, including four structural accessibility cases |
| SKIP | Visual screenshot acceptance | No reviewed baseline changes; no new visual tests |
| SKIP | Hosted Google/backend/exchange acceptance | Local/mock scope only; no live credentials, trading or deployment used |

The initial browser run exposed obsolete Shared Balances selectors in five starter
cases. Updating only those two selector expressions preserved existing coverage;
the current suite passed all 30 cases in 41.3 seconds.

No backend Swagger regeneration/runtime freshness, production account correctness,
new recovery-card screenshots across viewports, or full operator checklist is
certified by these local checks. Overview reporting follows the published backend contract. No commit or push.
