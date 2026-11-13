# MM automatic recovery and availability

Local follow-up2026-11-14: experimental funded Spot SELL admission during supported
uncertain Spot BUYs is implemented behind a default-false flag. Its native,
PostgreSQL and full-service qualification is recorded in the
[scoped recovery evidence](../../reports/performance/mm-quote-replacement-latency.md#scoped-uncertain-outcomes-local-qualification-2026-11-14).
This is not a connected rollout or completion of the broader fault matrix below.

Latest 2026-11-13: revision121/e635f3da7 deploys the readiness-wait correction.
The old technical HALT is recovered after independent verification, audit264,
with zero financial/risk changes. Start succeeds at control86, both/Random/
until-stopped. The00:13 local short check is running with active orders, no HALT,
no ERROR/restart/shutdown timeout and one probe timeout. Original99% quote
acceptance and recovery beyond the API history limit remain open. See the
[revision121 recovery](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision121-verified-maintenance-recovery-and-restart).

Earlier 2026-11-12: revision117/5c36bb7bd automatically clears the old verified
shutdown fence and returns to quotes on session/control84. Its next scheduled
profile change also returns to two-sided quotes without an observed restart or
deadline error. The short check retains unknown/guard samples; original cleanup
timing, the broad-suite liquidity failure and the30-minute gate remain open. See
[revision117 verification](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision117-verified-fence-recovery-and-next-profile-change).

The subsequent901-sample30-minute baseline is quality-valid but fails the
unchanged target: Spot98.36%, perpetual97.58%. No ERROR/restart/shutdown timeout
occurs; temporary fences and unavailable observations remain failures. Local
shared proof slots, stronger executor IDs, stage evidence and deterministic
freshness fixtures are qualified separately; new-image connected acceptance
remains open. See the
[follow-up record](../../reports/performance/mm-quote-replacement-latency.md#shutdown-proof-sharing-freshness-fixtures-and-executor-identity).

Earlier follow-up 2026-11-12: revision116 runs terminal publication and bounded
engine diagnostics, but a Random profile switch exceeded the8-second shutdown
deadline and its successor retained a drained `shutdown_pending` fence. Read-only
inspection finds a verified checkpoint with no pending/unapplied work and no risk
stop; the successor branch omitted that state. A PostgreSQL RED test reproduces
it; the local correction passes its focused checks, with a separate broad-suite
liquidity observation failure retained for investigation. Do not close this incident or
the connected quote gate from the earlier passing startup samples. See
[revision116 investigation](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision116-diagnostics-and-drained-shutdown-fence).

Earlier follow-up 2026-11-12: revision112/`1c1fa280a` runs the post-only CLOSE
correction; native readback confirms fresh-price replacements without same-executor
replay. Its completed901-sample window still fails99% (Spot86.84%, perpetual94.74%).
The bot is running without an incident HALT reset/owner restart. Probe causality
remains open; see [connected acceptance](../../reports/demo/cloudrun-quote-replacement-acceptance.md).

Status: r7 offline qualification passed. Cloud Run migration 021 is installed
and schema-validated. The approved drawdown policy remains 1,000/controller and
2,000/global, with peaks preserved. The 650-fill historical baseline was verified
and bound under audit 183; `cold_recovery_supported=true` survived subsequent
fills and two measured graceful rolling handoffs. The temporary-profile 30-minute
conditional quote target passed; its stricter zero-unknown gate failed one sample.
A subsequent regular-profile revision103 window has zero unknown samples but
fails the two-sided target (perpetual97.219%; Spot has no eligible samples).
The broader connected fault matrix remains open. The later revision110
close-first qualification verifies83 native maker LONG close fills and two cold
handoffs on continuous Start84. Its completed898-sample regular Random window
still fails99%: conservative Spot95.32%,perpetual92.65%, with unknown counted as
failure and guards excluded separately. See the
[connected close-first record](../../reports/demo/cloudrun-close-first-baseline.md).
See [policy application evidence](../../reports/demo/cloudrun-drawdown-1000-2000.md).
See [baseline and handoff evidence](../../reports/demo/cloudrun-runtime-baseline-handoff.md).
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
- [x] Verify the finite seven-case physical cgroup OOM subset on the current
  r7 follow-up image at 1 GiB: six verified MM resumes with working quotes and
  exact financial accounting, plus a legacy negative case that preserves its
  unresolved-effect HALT. This closes that local subset, not the broader
  connected matrix. Evidence is linked in the availability follow-up report.
- [x] Complete current revision108-based local follow-up coverage: native3,363
  plus16 subtests, PostgreSQL724, E2E36 plus the corrected host target, all four
  healthy load windows and the corrected six-fault target. Retain the failed
  original `all` runs. OPEN input and CLOSE restriction-decision cancellation
  scopes remain explicit; the older uniform CLOSE input bound is not declared met.
- [x] Negative tests: no blind POST retry, no lost/duplicate fill or fee, no stale
  owner mutation, no risk-latch reset, no resume after Stop, no unchecked queue growth.
- [x] Large-history regression exceeding current limits with the same one-intent
  fault; verify work per cycle depends on unresolved/new data, not account lifetime.
- [x] Run native/PostgreSQL fault tests and packaged offline E2E: the previous
  automatic-recovery release's r7 passed
  3,154 native tests plus 16 subtests, 686 PostgreSQL tests and all 34 E2E groups.
- [x] Repeat release/load/soak on one identified image: that previous r7 passed warmup 5 min,
  mixed 15 min, burst 90 s, Random soak 60 min and all six fault windows without
  relaxed budgets. Independent 180 s MM diagnostic passed; residual probe events
  and sampling limits are recorded in the linked evidence.
- [x] Install migration 021 with physical owner-lock exclusion and validate schema
  and all 21 SQL checksums on Cloud Run (2026-11-11); revision 91 is Ready
  and acquired ownership generation 460. At migration completion startup/live
  returned 200; the existing drawdown HALT was preserved and readiness returned 503.
  The subsequent approved policy re-arm and restart are recorded above.
- [x] Verify and bind the legacy baseline: complete venue fill/fee proof, native
  financial replay, isolated-clone negative cases and audited application.
- [x] Verify graceful Cloud Run owner handoff on the bound Demo ledger: unchanged
  session/control, automatic resume, no manual reset, and preserved financial
  checkpoint. This does not substitute for the remaining fault matrix.
- [ ] Complete broader connected Demo recovery/fault injection. Separate injected
  failures from natural venue errors; preserve guards and financial truth.
- [x] Measure the 30-minute live eligible two-sided quote objective and longest
  interruption separately from API uptime; classify one-sided, risk-blocked and
  unknown observations truthfully. Revision100: Spot99.323%, perpetual99.225%,
  counting the one unknown as failure; guard exclusions are not uptime.
- [x] Confirm a fresh zero-unknown observation window on the final observer fix:
  revision103,899 samples. This closes observation coverage, not quote availability.
- [ ] Meet the regular-profile two-sided target: revision103 perpetual97.219%
  fails99%; Spot has no eligible denominator. See the
  [cap / regular-profile evidence](../../reports/demo/cloudrun-perpetual-cap-qualification.md).

Proposed acceptance: zero manual DB edits for the supported fault matrix; exact
order/fill/fee/position reconciliation; automatic resume after verified recovery.
Target recovery within 30 seconds for resolvable cases once dependencies recover,
measured rather than implemented as a forced reset deadline. Persistent ambiguity
remains explicitly blocked. Existing 99% eligible two-sided observations over
30 minutes passed conditionally on revision100 as scoped above, not as a blanket
production or fault-matrix approval.

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


## 2026-11-11: bounded controller startup waits

At 16:40:51 ICT revision 106 latched `controller_task_stalled` during Random
Perpetual startup (`stage=startup`, age 5.324s). This precedes revision 107's
16:45 deployment of `a67c723f`; the continuous session had `expires_at=null`.
Revision 107 correctly retained that HALT. At 17:11 the service was still halted;
the initial investigation performed no recovery, reset, financial change or Cloud
deployment. The separately requested connected recovery is recorded below.

The old log cannot identify the particular startup await. Code review found
unbudgeted waits for the position-mode lock, mode write/confirmation and leverage
write, plus a stale progress timestamp after a completed slow read. TDD reproduces
these watchdog gaps without admitting any quotes while setup is incomplete.

- [x] Red: five controller/watchdog regressions fail on the previous source.
- [x] Green: bound each startup operation to 30s, declare that wait to the existing
  watchdog, refresh progress on completion and retain same-task cancellation.
  Mutation HTTP deadlines remain 5s; mutations are never automatically retried.
  Only transient reads, including mode confirmation, may retry.
- [x] Preserve real timeout/stall HALTs, lock ownership on cancellation and
  no order admission before account setup succeeds. Name the actual startup
  stage in future stall-confirmation logs.
- [x] Physical HTTP Red/Green: delay each of two serialized native account
  writes by 4s (each below its 5s HTTP deadline). The immutable revision 106 image
  HALTs at 5.353s; a two-file overlay on that same image restores both quote
  sides after an observed 9.309s account-setup wait, advances Random without
  replacing the owner/session and stops cleanly.
  This proves a matching code defect, not the unique historical slow RPC.
- [x] Continuous handoff and availability HTTP regression scenarios pass: same
  session/control after overlapping revision takeover, exact four-fill projection,
  optional observation/feed recovery without restart, and a genuine failed
  controller still HALTs. Native regression: 1,568 tests + 13 subtests; CLI: 1,370;
  logging: 22; focused stall diagnostic: 6. Pre-commit passes.
- [x] Deploy the fix, perform independently verified HALT recovery and observe
  actual Demo trading through the next normal Random step (see below). This
  bounded lifecycle qualification does not close the two-sided quote SLO.

Private reproducible evidence: `build/cloudrun-startup-wait-fix-20261111/`;
physical Red `build/e2e-offline/20261111-170856-123d87/`, Green
`build/e2e-offline/20261111-171533-a4c1d1`. Earlier 6.5s-response experiments
correctly failed with `controller_leverage_change_failed`: they exceeded the real
HTTP mutation deadline and are not watchdog Red/Green evidence.


Connected recovery on revision 108 (`ed7501bb1`): the operator requested removal
of this technical HALT. Native Stop reached control/ack 81. Check succeeded but
returned `recovery_reason_unsupported`; the generic runtime-task reason is outside
the shipping API allowlist. Its history limits/allowlist were not bypassed or
changed. A separate scoped maintenance proof independently matched all 1,334 fills
(570 Spot, 764 Perpetual) in two complete REST scans per market, extended the
previous audited 650-fill baseline by 684 exact fills using native accounting,
and matched Spot cash, LONG 0.05/SHORT 0.0472 BTC and the runtime financial/binding
hashes. All tracker entries were terminal; no pending intent, account mutation,
unapplied journal work, venue open order or risk stop was present.

With service writers stopped at MANUAL 0 and physical ownership released, the
advisory-locked preview passed. The atomic maintenance transaction removed only
`halt`, `halt_kind` and `reason`, adding audit 237 under maintenance authority.
It preserved 12,348 orders, 1,334 fills, 3 position rows, all risk checkpoints and
all other recovery fields. Original AUTOMATIC min 1/max 1 scaling, revision/image,
configuration and resources were restored. Start by the actual Basic admin reached
control/ack 82 at 17:43:25 ICT with `both`, `random`, `until_stopped`, a 600s step and
`expires_at=null`; credentials and risk limits were not changed.

Initial live REST confirms bot-owned post-only working orders on both markets.
Observed quotes are one-sided: Spot inventory sizing suppresses BUY, and Perpetual
BUY is blocked at the existing long cap. The cold startup has three ~301ms probe
timeouts and one 5.733s `stage=sleep/startup_stage=ready` confirmation warning;
these do not latch another HALT. The measured window then reaches step 1, waits
for readiness and resumes running on the same owner/session/control through
17:54:22 ICT: 60 samples, zero unknown or HALTed samples. Independent REST verifies
3 new Spot fills and 13 new Perpetual fills including original fees; native
positions match LONG 0.05/SHORT 0.0498 BTC. Spot retains 2 pending SELL orders;
Perpetual now reports `inventory_limit` on both sides because its 0.0002 BTC gross
headroom cannot fit a configured quote. The bot remains running with unchanged
limits. Retrieved logs through the final check have zero ERRORs, four ~301ms
self-recovered probe warnings in total, the startup confirmation warning and
two cap-related no-quote warnings. This is sampled trading/lifecycle evidence,
not uninterrupted quoting or the regular two-sided SLO.
Private receipts, preview/application proof and observations:
`build/cloudrun-startup-halt-recovery-20261111/`.

The subsequent open-item audit and qualification are tracked in the
[availability follow-up](../../reports/release/mm-availability-closeout.md).
It includes duplicate initial account writes and the independent cold-fill
projection defect when drawdown is disabled. Historical r7 passes above are not
transferred to the new five-file candidate; its OOM and release gates are rerun.

## Scoped uncertain-outcome follow-up — 2026-11-13

The reviewed50013 incident confirms that positive-evidence recovery still pauses
all trading indefinitely when the venue cannot establish an outcome. The desired
change is durable quarantine with background reconciliation and explicit degraded
operation. Never treat50013/51603, elapsed time, a missing ACK or an expired
send deadline as evidence of rejection. Existing risk, financial and ownership
fences retain authority.

### Admission contract

An unresolved order reserves its full possible effect, never an assumed zero
fill. Every current and restored uncertainty participates; bounds are count,
notional, account binding, instrument and immutable terms. The old client ID and
its quote level cannot be reused. Both pre-intent and final pre-transport checks
must agree. A worker must not independently grant trading authority.

Initial conservative qualification permits only funded Spot cash SELLs when all
uncertainties are managed Spot BUYs. Such a BUY cannot consume previously owned
base inventory; acquired base/proceeds are never credited before proof. Reserve
all resident SELLs against fresh base cash, include supported fees, and retain
the ordinary policy/price/inventory/ownership/Stop gates. All risk-increasing
orders and all Perpetual mutations remain blocked during that account uncertainty.
Unsupported fees/bindings/instruments, missing fresh observations or exceeding a
bound retain the account pause. This narrower supported set is deliberate;
general concurrent openings and Perpetual CLOSE require additional reservation
and financial-basis proofs before admission. They are not silently enabled.

The feature must ship disabled until its full offline fault matrix is green.
Default deployments retain the qualified global pause. No cloud env or risk
policy is changed as part of local implementation.

### Implementation and qualification checklist

- [x] RED:50013 retains one durable intent/reservation while a funded SELL can
  proceed and BUY/Perpetual remain blocked; no duplicate POST or same-level reuse.
- [x] Model bounded quarantine and per-order admission using exact native units,
  fresh account observations, all resident orders and supported original fees.
- [x] Keep native REST/WS verification and financial application before release.
  One inconclusive intent must not starve positive proofs for later intents.
- [x] Restore quarantine before admission on cold restart. Unknown GET outcomes
  run in background; strategy/API startup must not wait forever for that ID.
- [x] Persist/decode degraded status, affected scope, reservations, next retry and
  operator escalation. Liveness/startup describe service health; trading readiness
  continues to identify incomplete exchange proof.
- [x] Stop and graceful running-owner replacement retain unknown obligations and
  prevent stale authority from sending. A clean persisted uncertainty must not be
  relabeled as financial corruption or erased to make shutdown succeed.
- [x] Local finite subset: managed-key Disconnect stays pending and retains its
  encrypted key across retries; timed profile rollover preserves quarantine;
  physical1GiB cgroup engine OOM restores two unknown BUYs and one original-fee
  partial SELL fill exactly once. Existing ordinary ownership/risk gates remain.
- [ ] Repeat these lifecycle cases on the intended connected revision. The local
  subset does not cover every crash boundary or OOM during Disconnect itself.
- [x] Test the bounded supported cash-SELL matrix: lost/late ACK, late/duplicate
  fills, false51603, simultaneous native sends, stale balances, original fees,
  exact count/notional bounds, persistence failure, takeover and risk stops.
  Physical PostgreSQL verification-COMMIT loss covers both server outcomes;
  SIGKILL covers send/fill/hold with the flag both off and on. This closes the
  listed finite cases, not every interleaving or OOM during Disconnect. See the
  [matrix evidence](../../reports/performance/mm-quote-replacement-latency.md#bounded-scoped-fault-matrix--2026-11-14).
- [x] Qualify the initial native/PostgreSQL/full-service offline subset. Preserve
  the initial whole-service failure and the proposal/native quantization fix.
- [ ] Deploy explicitly and observe one stable revision for30minutes.
  Existing99%/probe failures remain. The rollout flag remains false.
- [ ] General concurrent openings/Perpetual CLOSE: extend only after independent
  worst-case shared-collateral, hedge-quantity and cost-basis proofs are tested.
