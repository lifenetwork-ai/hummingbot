# MM automatic recovery and availability

Status: r7 offline qualification passed. Cloud Run migration 021 is now installed
and schema-validated. Revision 93 now runs Demo after the operator-approved
drawdown policy increase to 1,000/controller and 2,000/global; the old risk stop
was explicitly re-armed with peaks preserved. Historical baseline and controlled
Demo recovery/quote acceptance remain open (`cold_recovery_supported=false`).
See [policy application evidence](../../reports/demo/cloudrun-drawdown-1000-2000.md).
Scope: existing single Cloud Run service, PostgreSQL and native connector/recorder/V2
execution. Preserve ownership fencing, financial evidence, risk limits and audit.

## Problem established by code and the Demo incident

The following describes the behavior before this implementation.

- `BotOwnership.order_failed` latches global HALT after an uncertain sent order.
  Preserving its pending tracker is correct; there is no automatic resolver that
  proves the outcome and reopens admission. `require_recovery` conflates technical
  uncertainty with an operator-latched stop.
- A replacement owner treats pending intents or an unfinished runtime as a HALT.
  The latest fix keeps the stopped engine/API available, but does not restore trading.
- Recovery snapshots scan lifetime history, capped at 5,000 orders / 2,000 fills.
  The actual 10,295-order account cannot use that path even for one pending intent.
- Fills and V2 held-position projection do not have one crash-consistent application
  boundary. The incident required rolling forward 73 recorded Spot fills after
  proving the stored checkpoint against the native held-order journal.

Evidence: [approved incident recovery](../../reports/demo/cloudrun-busy-submission-recovery.md).
These are recovery design gaps, not justification for removing the safety checks.

## Operational states and authority

| State | Admission and recovery |
| --- | --- |
| Running / degraded | Retry transient reads/reconnect with bounded backoff; admit orders only under the existing fresh-data, ownership and risk checks. |
| Reconciling | Persist the uncertain effect, pause affected risk admission, resolve through bounded REST/WS evidence, then resume automatically only after durable verification. |
| Manual HALT | Preserve risk latches, inconsistent/unprovable financial state and account-binding failures for explicit recovery. Publish the exact blocker. |

Ownership loss immediately fences every mutation by the old owner, including
cancellation. A successor may reconcile after acquiring authority. A technical
pause is not permission to bypass a drawdown stop, failed persistence or stale feed.
Spot and perpetual sharing an account must initially share the affected risk scope;
instrument-level isolation needs proof of independent balances and exposure.
Known orders may be managed only while ownership and the applicable authority hold.
Transient errors do not authorize automatically flattening perpetual positions.

Keep startup/liveness available during reconciliation. Readiness, quote diagnostics
and trading availability must show the actual pause; a healthy API is not evidence
of working quotes. Persist reason, scope, last successful progress, next retry,
evidence cursor and whether operator action is required. Bound repeated log output.

## Phase 1: durable financial application and attempt records

- [x] Red: kill the process after fill commit and before held-position transfer;
  restore the same quantity, original fees, basis and realized PnL exactly once.
- [x] Define a canonical financial application journal with monotonic sequence,
  account binding, order/fill identity and checkpoint watermark. Persist the fill
  and its durable projection work atomically; process that work idempotently.
- [x] Preserve V2 active-executor versus held-position accounting. Do not add a
  second position update per fill that double-counts exposure or changes native
  transfer ordering/cost basis. Prove active-to-held transitions and restart replay.
- [x] Establish a verified migration baseline for existing ledgers; insufficient
  history remains blocked rather than silently inventing an opening position.
- [x] Persist an immutable submission attempt before transport: client ID, payload
  hash, account/environment binding, ownership generation, applicable session,
  send deadline and risk reservation. Record structured transport/venue receipts.
- [x] Model prepared, possibly-sent, acknowledged, rejected and terminal outcomes;
  crash between preparation and transport is conservatively possibly-sent unless
  durable evidence proves otherwise. Never reuse client IDs for a new attempt.
- [x] Cover unknown DB commit outcome by reading durable identity/state before
  replay. Restore reservations for every concurrent possibly-sent attempt.

## Phase 2: automatic reconciliation and transient isolation

- [x] Red: acknowledged order with a lost response, partial/full fill before ACK,
  late private WS event and terminal cancellation all resolve without manual DB
  repair, duplicate order submission or duplicate financial application.
- [x] Classify verified pre-send rejection, definitive venue rejection, transient
  read failure and ambiguous mutation separately. Classify exact typed receipts;
  HTTP 429/5xx or a text match alone is not a no-effect guarantee.
- [x] Add an ownership-fenced reconciler using existing native tracker and ledger
  methods. Bounded concurrency, queue, request deadlines and backoff with jitter;
  keep it outside the engine tick and survive process replacement via durable work.
- [x] Merge positive REST/WS status and fills idempotently. Persist the resolved
  outcome and position work before releasing reservation/admission. Revalidate
  current ownership, control, credentials, session, freshness and risk before resume.
- [x] Preserve a newer Stop/Disconnect/expired session throughout reconciliation;
  completion must never restart trading against the latest operator intent.
- [x] Treat not-found as inconclusive for a possibly-sent order. No automatic
  success after N retries, a quiet WS interval or expiry of the send deadline.
  A deadline bounds future processing, not proof of no earlier execution.
- [x] Specify adapter-specific evidence for proving absence. If that proof cannot
  be obtained, keep the reservation/pause and expose an actionable escalation.
- [x] Test recovery of cancel/account mutations too; avoid repeating a mutation
  just because its HTTP response was lost. Pause and prove each affected effect.
- [ ] Optional later extension: permit bounded activity under unresolved outcomes
  only with a verified worst-case balance/exposure reservation model covering all
  concurrent sends. Cap unresolved count/notional and stop the failing channel.
  This is not part of the first automatic-resume implementation.

The [OKX API contract](https://www.okx.com/docs-v5/en/) enforces client-ID uniqueness
among pending orders rather than across all historical submissions. Therefore
resending with the same ID is not a general exactly-once transport guarantee.

## Phase 3: incremental recovery and operational acceptance

- [x] Recover from the verified checkpoint watermark plus unresolved effects and
  new fills, with bounded pages/time per worker cycle. Account age must not cause
  the 5,000-order lifetime cliff. Do not merely raise the current limits.
- [x] Maintain complete evidence coverage across pagination and bounded venue
  retention; a missing interval or invalid baseline blocks financial repair.
- [x] Reuse admin preview/confirm, fingerprints and audit for manual cases; expose
  automatic progress/outcomes through status with sanitized structured reasons.
  Include initiator, ownership generation, evidence and resume decision in audit.
- [ ] Fault matrix: process death/OOM/deploy at every send/ACK/fill/commit/checkpoint
  boundary, owner handover, delayed/duplicate/out-of-order events, stale not-found,
  intermittent REST/WS/DB failures, queue saturation and shared-account exposure.
- [x] Negative tests: no blind POST retry, no lost/duplicate fill or fee, no stale
  owner mutation, no risk-latch reset, no resume after Stop, no unchecked queue growth.
- [x] Large-history regression exceeding current limits with the same one-intent
  fault; verify work per cycle depends on unresolved/new data, not account lifetime.
- [x] Run native/PostgreSQL fault tests and packaged offline E2E: r7 passed
  3,154 native tests plus 16 subtests, 686 PostgreSQL tests and all 34 E2E groups.
- [x] Repeat release/load/soak on one identified image: r7 passed warmup 5 min,
  mixed 15 min, burst 90 s, Random soak 60 min and all six fault windows without
  relaxed budgets. Independent 180 s MM diagnostic passed; residual probe events
  and sampling limits are recorded in the linked evidence.
- [x] Install migration 021 with physical owner-lock exclusion and validate schema
  and all 21 SQL checksums on Cloud Run (2026-11-11); revision 91 is Ready
  and acquired ownership generation 460. At migration completion startup/live
  returned 200; the existing drawdown HALT was preserved and readiness returned 503.
  The subsequent approved policy re-arm and restart are recorded above.
- [ ] Verify the legacy baseline and run controlled
  Demo recovery/fault injection. Separate injected failures from natural venue
  errors; preserve guards and financial truth.
- [ ] Measure the 30-minute live eligible two-sided quote objective and longest
  interruption separately from API uptime; classify one-sided, risk-blocked and
  unknown observations truthfully. Offline diagnostic samples do not close this.

Proposed acceptance: zero manual DB edits for the supported fault matrix; exact
order/fill/fee/position reconciliation; automatic resume after verified recovery.
Target recovery within 30 seconds for resolvable cases once dependencies recover,
measured rather than implemented as a forced reset deadline. Persistent ambiguity
remains explicitly blocked. Existing 99% eligible two-sided observations over
30 minutes remains a separate qualification; this plan does not claim it passed.

Implementation order: financial durability and durable attempt state, automatic
reconciler/classification, incremental recovery, then connected fault qualification.
Do not interrupt the currently running Demo to prepare or review this plan.

## Implemented scope and remaining qualification

The worker supports managed OKX Spot/perpetual MM PositionExecutor maker entries
without exit barriers, with matching account/environment/session context. Unknown
create/cancel outcomes can resolve from positive exact order and complete original
fill evidence. Unsupported executor scopes and ambiguous account-setting mutations
retain manual protection; an ACKed setting change is not blindly repeated. There
is no general automated absence proof for a possibly-sent order: deadline expiry
and repeated 51603 stay inconclusive, with visible operator escalation after 300 s.

Migration 021 establishes no historical application markers. Empty ledgers can
initialize a baseline; reviewed complete recovery may bind an existing ledger.
A legacy ledger beyond the manual API history limit still needs independently
reviewed one-time maintenance. Once bound, incremental handover reads outstanding
work rather than lifetime history (tested with 10,000 historical terminal orders).
The optional worst-case trading/reservation extension remains deferred.

SIGKILL is tested at send/fill/held-transfer boundaries; a real response-loss proxy
tests delivered COMMIT versus lost COMMIT reply. This is not a claim of actual
kernel OOM or every Cloud deployment interleaving. The remaining broad fault-matrix
checkbox intentionally includes that connected qualification.

Positive physical HTTP fault E2E restores both quote sides in 1.125 s, preserves the
owner, makes no duplicate submission and stops cleanly. The absent-order HTTP429
case stays reconciling without a manual HALT or POST retry and preserves the pending
intent after Stop. These are simulated venue tests, not actual OKX account fills.

Current verification and rollout limits: [implementation evidence](../../reports/performance/mm-automatic-recovery.md).

The final integration regressions also cover managed sessions with drawdown
disabled and advancement of an existing MM baseline by credential-cleanup fills.
Cleanup advances only a baseline matching financial state before the transaction;
it never certifies pre-existing drift. Migration 021 requires a writer-free schema
installation before compatible rolling takeover; it is not an online migration
while an older image owns trading.

Connected rollout evidence: [Cloud Run schema cutover](../../reports/platform/cloudrun-auto-recovery-migration-20261111.md).
