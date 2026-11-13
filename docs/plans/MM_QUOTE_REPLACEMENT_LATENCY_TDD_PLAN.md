# MM quote replacement latency

Scope: optimize the verified revision110 path without changing caps, fee-inclusive
breakeven, terminal proof, native financial commits or quote qualification.
Existing Demo runs and failed quote windows retain their original scope.

The regular Random window has16 Spot and57 perpetual missing-quote samples;
the conservative two-sided fractions are95.32%/92.65%. Twenty native replacement
pairs have median1.1915s and maximum5.508s between last fill and next venue
creation. Those timestamps do not identify a unique code bottleneck or prove
continuous quote downtime.

Code inspection finds an unconditional5s sleep inside order-executor shutdown,
plus its normal loop sleep. A cancel ACK can update the order during that wait
without waking the task. Managed closing quotes will use a coalescing wake event;
legacy executors retain their timing. Waking a loop grants no new order authority.

## Checks

- [x] RED: cancel ACK during the5s wait must retire a managed CLOSE promptly,
  retaining all original and late partial fills. Include ACK-before-wait,
  both hedge directions, duplicate/unrelated events and cancellation cleanup.
- [x] GREEN: wake managed executor waits on relevant state changes; keep legacy
  waits and the indeterminate-outcome fence. Commit remains the capacity boundary.
- [x] Verify a completed order does not leave a managed loop sleeping; an ACK
  without financial commit cannot release the controller level.
- [x] Reproduce and remove the extra managed-controller scheduling wait after
  publication, with coalescing, no busy loop and no concurrent control cycles.
- [x] Add bounded stage timing to the existing opt-in offline activity collector
  for terminal callbacks, native checkpoint and subsequent quote processing.
- [x] Run focused executor/controller/financial tests and disposable PostgreSQL
  commit/rollback/recovery checks. Qualify the packaged candidate separately.
- [x] Review profile preparation and status/liquidity sampling delays using
  retained evidence. Preserve the shutdown fence and unknown-as-failure rule.
- [x] Deploy the earlier correction and repeat connected quote qualification.
  Revision111/112 windows retain their failures; deployment is not a99% pass.

## Additional incident found during connected observation

At04:28–04:30UTC on2026-11-12, revision110/owner483 is not trading:
`bootstrapping_recovery` waits on a Spot creation intent with a durable HTTP200,
matching client ID, empty venue order ID and `sCode=50037` receipt. The existing
classifier recognizes50036 only. Repeated51603 reads then remain inconclusive.
The startup retry path also fails to raise `operator_required` after five minutes.

- [x] RED four classifier/connector cases for50037, then GREEN for both50036/50037.
- [x] RED PostgreSQL replay and startup escalation; reject only exact saved
  receipts after current account/environment validation. Keep ambiguous receipts,
  local fills and acknowledged venue identities protected.
- [x] Resolve intent, saved tracker and runtime audit in one transaction;
  interruption rolls all three back. Native FAILED callback follows commit.
- [x] Verify a real successor owner resolves the receipt with no second POST and
  no financial/checkpoint change; ordinary51603 still pauses and requests review.
- [x] Startup and post-cancellation bootstrap waits use the same attention policy.
  Operator presentation requests support without inventing a HALT or reset button.
- [x] Deploy the qualified changes and verify the existing blocked intent follows
  the native recovery path, then repeat connected quote qualification.

Do not mark this incident fixed on Cloud Run from local tests. No manual Cloud DB
update or trading Start has been issued in this follow-up.

## Local outcome

Main regression:3,483 tests plus16 subtests pass. All738 PostgreSQL tests pass;
the17-case expiry selection includes two additional final native-transport cases.
The final stopped-Start attention edge was RED before correction; its16 operator
and API tests pass. Pre-commit and whitespace checks pass.

The300-second diagnostic image probe passes its original budgets with300 matching
fills, four actual closing fills across both hedge legs and verified managed-key
cleanup. Four matched terminal→financial-commit→next-controller observations are
retained. The final image differs only in the operator presentation edge and
passes a real-network simulator lost-ACK E2E: recovery2.97s, same owner, no duplicate
submission, quotes restored. This is scoped local acceptance, not a full new soak
or a connected quote-target pass. See the
[evidence record](../../reports/performance/mm-quote-replacement-latency.md).

## Connected revision111 and post-only follow-up

The50037 intent is resolved automatically with exactly one runtime audit; bot
control84 continues on owner484. The completed901-sample regular Random window
is quality-valid but fails99%: Spot93.46%, perpetual87.89%. The original classifier
and all unknown/guard categories are retained. No incident HALT reset or owner
restart occurs; transient profile-shutdown fences remain failing observations.
See [connected acceptance](../../reports/demo/cloudrun-quote-replacement-acceptance.md).

- [x] Prove native cancelSource31 and nineteen same-price attempts by one CLOSE
  executor; separate this from an unknown submission or a financial mismatch.
- [x] RED four live-cancel cases, then retire managed maker CLOSE through native
  terminal/financial application rather than replay its prepared price.
- [x] Verify partial/zero fills, both sides, duplicate/foreign events and the
  unmanaged path. Native3,491+16, PostgreSQL35 and both packaged hedge cases pass.
  The old image fails; its failed run is retained. No proof or risk guard is relaxed.
- [x] Deploy this additional local correction and repeat connected qualification.
  Revision112 confirms269 CLOSE orders from269 executors and five native
  post-only cancellations followed by new executors/prices. No manual reset.
- [ ] Close the unchanged99% target: the new901-sample window still fails
  Spot86.84%/perpetual94.74%, with35 probe timeout transitions. The timeout values
  are deadline-censored; cached report progress around15/18 overlapping probes
  is scoped evidence, not uninterrupted ticks. Preserve the two shutdown-fence
  samples as unknown and retain bounded timing/stack capture as follow-up.

Three event-clock timestamps exceed the manual verifier's one-second allowance;
retained native snapshots still match the actual execution times. Keep that
separate limitation visible without rewriting history or widening the tolerance.

## Follow-up: production diagnostics and terminal publication

Keep revision112's connected results intact. The following work is local until
a separately verified artifact is deployed; no Cloud policy or trading mutation
is part of this implementation.

- [x] RED native executor completion/cancellation without another engine tick:
  terminal financial application must precede controller publication. Include
  partial fills, rollback/retry, duplicate bursts, stale session and shutdown.
- [x] Coalesce managed terminal notifications on the existing strategy loop,
  using its unchanged native report/checkpoint path. No extra order authority,
  independent financial writer, report polling loop or clock acceleration. The
  one-shot50ms notification window batches adjacent ACKs; a regular report
  consumes any queued notification. Callback context excludes retired order and
  cleanup authority, including a RED/green inheritance test.
- [x] Add opt-in bounded engine heartbeat/stage/SQL diagnostics on one monotonic
  clock, with sanitized stack summaries. Capture neither locals, SQL/parameters,
  credentials nor raw arguments. Rate-limit WARNING evidence independently of
  the project's missing INFO; telemetry failure must not alter trading results.
- [x] Verify real loop stalls, task cancellation, SQL exceptions, retention,
  log budgets, disabled overhead and complete hook/task/thread teardown.
- [x] Run native regression, PostgreSQL terminal/financial tests and packaged
  LONG/SHORT replacement cases. Preserve all failed evidence and scope limits.
- [x] Deploy the candidate with diagnostics explicitly enabled and retain actual
  bounded timing/stack packets. Revision116 confirms SQL hooks are attached;
  initial stacks include startup import and synchronous SQL receipt persistence.
- [x] Deploy the verified drained-fence successor correction. Revision117 clears
  the old fence on control84 and returns to quotes again after its next scheduled
  profile change; no manual DB/reset operation. Retain3 unknown samples per market
  in the additional101-sample short observation, not a30-minute target pass.
- [ ] Investigate the original8-second cleanup timeout, remaining probe episodes
  and the broad-suite liquidity failure, then repeat the unchanged connected target.

Local qualification:3,523 tests plus16 subtests,62 related PostgreSQL tests and
both packaged hedge-leg scenarios pass. The final diagnostic image also passes
the unchanged300-second load budgets at2CPU/1GiB:300 matching fills, both-leg
closes and verified flat managed-key cleanup. This remains scoped offline
acceptance; see the [qualification record](../../reports/performance/mm-quote-replacement-latency.md#terminal-publication-and-production-diagnostics-follow-up).

## Shutdown/freshness follow-up

- [x] Capture freshness evidence at the API's exact read. Retain the original
  liquidity failure and the subsequent cold credential-status failure.
- [x] RED a delayed HTTP-server setup: snapshot/heartbeat6.0048s old correctly
  produces unavailable. Refresh fixture publications after setup using the DB
  clock; keep stale heartbeat, future timestamps and exact5s boundary coverage.
  This proves fixture fragility, not the unrecorded predicate in the first failure.
- [x] RED cross-connector proof starvation with eight tracked orders. Share the
  existing three read slots across connectors; retain fresh proof before/after
  checkpoint and cancel/drain siblings on any failed/canceled proof.
- [x] RED bounded shutdown-stage evidence and verify each waiting stage keeps
  the fence, including an actual8s deadline with eight tracked orders. Preserve
  deadline, financial, owner and risk checks; this is not a retroactive unique
  explanation of revision116's timeout.
- [x] Reproduce the regression-discovered executor ID collision by fixing the
  old five-digit nonce. New IDs use128-bit entropy with the same wire format;
  serialized/explicit historical IDs remain unchanged. Queue-bound assertions
  remain100, not an adjusted expectation for collisions.
- [x] Complete the unchanged30-minute baseline on deployed revision117:
  901 samples, quality pass, target fail (Spot98.36%, perpetual97.58%). Keep
  all unknowns/guards and the original classifier. No manual reset or Cloud write.
- [x] Finish final-image LONG/SHORT qualification, then commit the reviewed local
  changes and their exact evidence. The local image is not the Cloud117 artifact.
- [x] Deploy the qualified changes as revision118/1e5d15bda and repeat the
  unchanged connected window:897 samples, quality pass, target fail
  (Spot94.50%, perpetual94.03%). Retain all failed samples and the API latency tail.
- [ ] Meet the unchanged99% target after follow-up deployment. The original8s
  incident still has no retrospectively proved unique cause. See the
  [connected evidence](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision118-connected-window-and-position-poll-follow-up).

## Native position-poll scheduling follow-up

- [x] RED a full account poll whose history remains pending after positions
  completes. A later stale-position refresh must run before history completes.
- [x] Service a coalescing position event inside the existing native poll.
  Keep one sequential position reader; do not repeat history/status/balance
  reads for each position request or create another persistent poll worker.
- [x] Cover requests during an in-flight position read, burst coalescing,
  history/position failure and parent cancellation with complete child drain.
  Preserve native snapshot/WS-version proof and freshness/cap/financial rules.
- [x] Qualify3,541 native tests plus16 subtests and final-image LONG/SHORT
  packaged cases, including partial fills and cold restart. Scope and artifacts
  are in the [local follow-up record](../../reports/performance/mm-quote-replacement-latency.md#native-position-poll-scheduling-follow-up).
- [x] Deploy the poll correction as revision119/709700c70 and repeat901 samples:
  quality pass, unchanged quote target fail (Spot89.61%, perpetual87.59%). Retain
  two unavailable-position samples and all guard/unknown observations.

## Scoped absent-leg proof follow-up

- [x] RED: a LONG WS update during full REST preserves the newer LONG and must
  not discard independent proof that SHORT is absent. Cover the reverse direction.
- [x] Keep whole-pair REST/WS fencing and full-snapshot timestamps unchanged.
  Add per-leg absence evidence only for a complete successful scoped response,
  with exact instrument, CROSS/HEDGE, valid unique sides and finite fields.
- [x] Same-leg WS invalidates old/in-flight absence proof; net/unknown side
  invalidates both. Invalidate changed-leg metadata before WS parsing so a
  malformed/canceled event cannot reuse an old fresh observation; retain quantity.
  Positive REST presence clears old absence evidence. Stale,
  future, failed and incomplete evidence cannot borrow a fresh pair timestamp.
- [x] RED: invalid leg evidence must request a native refresh even when the pair
  timestamp is fresh. Validate cached venue evidence before skipping refresh,
  preserving the regular polling cadence and unchanged admission age limit.
- [x] Serialize native REST position callers, including cancellation/retry, so
  an older response cannot arrive last and renew obsolete proof.
- [x] Qualify21 new native cases,3,562 canonical tests plus16 subtests and exact
  final-image LONG/SHORT partial/cold-restart scenarios. The deployed window and
  new local artifact remain separate; see the
  [record](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision119-connected-window-and-per-leg-absence-proof).
- [x] Deploy scoped absence proof as revision120/847f38e1e; startup check passes.
- [ ] Repeat connected qualification after the independent startup-readiness
  HALT is resolved. Capture the exact failed predicate if position evidence fails.
- [ ] Resolve ordinary replacement gaps, profile-handoff unknowns and narrow
  shutdown proof margin without changing the original99% qualification rules.

## Connector readiness outage follow-up

- [x] Reproduce the revision120 failure: passive readiness consumes the ordinary
  reconciliation deadline and creates durable HALT before open-order reads.
- [x] Keep readiness outside that deadline, on native reconnect tasks, with no
  send authority. Preserve ownership checks and Stop/Switch cancellation.
- [x] Keep the30s post-readiness reconciliation/cancel deadline, prior HALT and
  all unresolved-order/intent/financial verification authoritative.
- [x] Test service startup/live200 versus ready503 during the wait and actual Stop;
  verify durable state against disposable PostgreSQL.
- [x] Deploy as revision121/e635f3da7. Stop/Check exposes the existing history
  limit; independent audited-prefix/native-journal verification permits delegated
  maintenance recovery with zero financial/risk changes, audit264. Normal Start
  succeeds at control86; retain the failed API receipt and short-check limits.
- [x] Implement and qualify locally reviewed checkpoint recovery above the lifetime
  order/fill caps, preserving financial/risk state. See
  [recovery follow-up](ADMIN_RECOVERY_API_TDD_PLAN.md#verified-checkpoint-history-follow-up--2026-11-13-local).
- [x] Verify revision122 deploys the recovery API follow-up, exact commit/digest
  and100% traffic. No new recovery request was issued to the running bot.
- [ ] Exercise that API against a qualifying stopped technical incident; the
  previous separate maintenance recovery remains the actual recovery evidence.
- [ ] Repeat the unchanged30-minute quote gate on one revision/session. See the
  [incident record](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision120-connector-readiness-outage).


## Revision122 Spot terminal proof during profile handoff

- [x] Capture the10:31:48 local shutdown failure and one engine child restart,
  on the same revision/session/control86, as Random advances its600s profile step.
- [x] Read-only native/DB comparison: four BUYs before the incident have matching
  identity/terms; two are canceled without fills and two are fully filled. Their
  later native order/fill reads are complete. The original failing predicate was
  suppressed by the old guard, so these later reads do not establish its cause.
- [x] RED: full native execution plus committed recorder receipt still fails the
  terminal gate if a redundant REST read is unavailable.
- [x] Reuse exact committed native-fill proof for a fully filled BUY, preserving
  charges and checking account binding, owner and recorder health. Unknown,
  partial, uncommitted, expired or conflicting proof retains REST and all fences.
- [x] PostgreSQL handoff boundary, rollback and lost-owner tests; native exactness,
  fee/quantity/identity conflicts, stage diagnostics and redaction regressions.
- [x] Log the per-order proof stage and error type on failure transitions; expose
  a fixed scoped incident reason rather than only terminal-unavailable.
- [x] Deploy this follow-up and observe actual profile handoffs on one
  revision/session: revision124/`64e78147d`, generations68→69→70, two scheduled
  handoffs including one full600s interval after Spot BUY fills. No observed
  HALT, terminal-proof error or engine restart through04:53UTC. Seven probe
  timeouts self-recover; the unchanged30-minute quote gate stays open. See the
  [connected record](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision124-connected-spot-terminal-handoff-follow-up).

No spending cap, drawdown policy, perpetual cap, shutdown timeout, credential or
Cloud setting is changed. No schema/env/API route change is required. Ordinary
terminal proof remains authoritative for canceled/partial or uncertain orders.


Local qualification: **1 RED** reproduces redundant-read dependence despite a
complete committed fill. Broader shipping regression: **3,623 passed +16
subtests**; PostgreSQL ownership/recorder/recovery/COMMIT-boundary regression:
**110 passed**. Final affected native/service rerun: **78 passed**; final Spot
PostgreSQL rerun: **19 passed**. These scopes overlap and are not added together.
All hooks pass. The Cloud incident's original per-order predicate remains unknown;
this fixes the demonstrated complete-commit failure class and adds diagnosis for
remaining failures. Revision124 adds bounded connected handoff evidence; it does
not identify the original suppressed predicate or close the quote SLO.
Qualification evidence is retained locally under
`build/spot-terminal-handoff-20261113/` and
`build/spot-terminal-postdeploy-20261113/`.

## Revision124 probe SQL follow-up

- [x] Inspect the retained04:38:35–04:53:06UTC diagnostic packets. Seven sampled
  heartbeat stalls have gaps255.986–281.693ms: six stacks are in synchronous
  PostgreSQL waits (send-attempt execute/COMMIT, terminal recording and flush
  fencing), one in startup import. These are sampled locations, not proof that
  each explains an entire probe timeout; two send-path samples overlap probes
  directly. No probe threshold is changed.
- [x] RED five terminal-status cases: Order, attempt and history unnecessarily
  flush separately before the tracking checkpoint (four flushes). Suppress
  autoflush only for the attempt/history reads that do not depend on those
  pending changes. Checkpoint flush and final commit retain their owner fences.
- [x] GREEN cancellation/completion/expiry/failure, duplicate history, uncertain
  attempt retention, physical owner loss between writes, and final COMMIT error.
  Verify transaction rollback covers order, attempt, status and tracking together.
- [x] Add a bounded `db.commit` diagnostic span: cursor hooks exclude its reply
  wait. RED two missing-span contracts; GREEN actual COMMIT success/deferred
  constraint failure, telemetry failure and existing I/O/unknown-outcome tests.
- [x] Complete related native/PostgreSQL regression:107 native recorder/tracker
  tests +5 subtests,45 diagnostic/deadline/lifecycle tests,182 related PostgreSQL
  tests and18 COMMIT/diagnostic/I/O PostgreSQL tests. Scopes overlap. Paired
  cancellation uses15→13 SQL statements; local timing is component evidence,
  not proof of a whole-engine/probe improvement. Final affected rerun:13 passed.
  Alternating-pair medians22.879→18.805ms, or147.923→134.971ms with5ms injected
  per statement. All source hooks pass.
- [x] Verify revision126 deploys both corrections and the `db.commit` span at
  unchanged300ms probe limit. The bounded05:36–05:45UTC window retains8 probe
  timeouts,13 completed checkpoint spans /0 errors, one transient session fence
  and no engine restart. See the [connected record](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision126-terminal-write-and-checkpoint-batch-deployment).
- [ ] Establish comparable connected latency/quote improvement. Keep import,
  send/terminal waits and quote-target failures separate; the bounded window
  does not prove that all future probes meet300ms.

Evidence: `build/probe-sql-followup-20261113/` and
`build/checkpoint-batch-postdeploy-20261113-1239/`. Risk settings, shutdown budget
and probe policy are unchanged; no recovery/trading mutation is used for review.

## Held checkpoint metadata batching

Existing position snapshots and shutdown executor groups already commit in
batches. The remaining N+1 path is `checkpoint_held_positions`: executor/order
metadata reads and applied-fill journal updates repeat for each held executor.

- [x] RED a six-executor transfer: six metadata reads where one batch suffices.
- [x] Prefetch Executor/Order rows and update fill-journal application in groups
  of at most100 IDs, on the caller's original transaction. Shared ORM objects
  retain the duplicate-order fence; no proof is cached across callbacks.
- [x] Retain the original path for one executor/one order. A preliminary25-pair
  batch experiment reduces SQL there but makes the component slower. Batch only
  multiple executors or multiple orders; separately cover one executor with two
  orders. The final single case retains23 statements and comparable timing.
- [x] Coalesce pending writes while validating committed fills. Keep the1001-row
  sentinel and exact native fee/quantity/identity checks separately per order.
- [x] Cover1/6/101 members, mixed existing/new executors, duplicate application,
  corrupt final members, missing orders, duplicate-order use, owner loss and
  failed COMMIT. Chunks do not introduce independent commits. Keep hold-journal
  order identical to native application order. Test the corrupt final member
  across the100-ID chunk boundary.
- [x] Require a final fresh physical-owner check after the last flush, including
  a legacy baseline without a RuntimeCheckpoint. RED catches a lost owner after
  coalesced writes in that branch; GREEN rolls back all members.
- [x] Measure against the exact committed pre-batch method. Final alternating
  warmed component pairs: one member23→23 statements /31.206→30.926ms (25 pairs),
  six members78→26 /105.919→44.712ms (five pairs),32 members364→52
  /408.996→95.624ms (five pairs). Six-member fill reads remain six;
  executor/order lookups6→1 each and fill journal updates6→1. Retain earlier
  noisy/preliminary measurements; infer no Cloud/probe speedup from local timing.
- [x] Complete final regression:41 PostgreSQL batch/commit/reduction tests,
  plus the separate single-executor/two-order case;101 native tests +5 subtests.
  The earlier related pass has176 PostgreSQL tests. Scopes overlap. Three group
  measurements and the final25-pair single-path check pass; all hooks pass.
- [x] Verify deployed revision126's exact image, native checkpoint progress and
  generation74→75 handoff. Retain its transient stale/fenced sample; no restart
  or persistent stop. Live spans lack batch-size metadata, so they do not prove
  a particular multi-executor SQL reduction on Cloud.
- [ ] Close the remaining probe and unchanged30-minute quote target using
  comparable connected evidence; local batch timings do not close either gate.

No timer, queue or delayed financial acknowledgment is introduced. Native held
application order and publish-after-COMMIT remain unchanged. Evidence is retained
under `build/checkpoint-metadata-batch-20261113/`.

## Terminal read and startup loop fairness

- [x] Retain revision126's eight initial timeouts and sampled stacks. The348ms
  terminal span's detailed children have already left the bounded ring; do not
  invent a unique breakdown. Separate observed durable-fill SQL waits, startup
  report assignments, owner/write waits and other Python activity.
- [x] RED terminal SQL prevents loop progress, owner/native changes are missed
  during a slow read, and cancellation can return before the connection closes.
  Keep native receipt/fee/quantity/identity checks and fresh owner authority.
- [x] Use one concurrent read-only receipt job on its own pooled connection;
  keep the original three-slot REST proof limit. The physical owner and native
  financial writes remain on the loop. Deep-copy proof inputs; recheck current
  owner, recorder, native fills and private state after every awaited proof.
- [x] RED loop-wide cancellation of an internal child Task can detach its
  thread. Use a shielded executor Future; drain it on repeated cancellation
  before releasing the DB slot, terminal slot or lifecycle tracking.
- [x] Real PostgreSQL400ms SELECT / native `/live` HTTP300ms test: synchronous
  control times out; the candidate serves200 and heartbeat progress. No native
  position/intent is changed; the physical owner backend stays on its loop.
- [x] RED startup assigns report models per historical row and publishes a
  prefix before a malformed row fails. Accumulate in plain per-controller state
  in original Decimal order, then publish totals after full stream validation.
  Retain finite-value, close-type, hold exclusion and cursor-close behavior.
- [x] Seven alternating warmed CPU-only pairs:20,000 projected rows64.087→
  48.892ms median; one row0.008→0.011ms. Exact report equality, not a Cloud speedup.
- [x] Final shipping native regression:3,638 passed +16 subtests. Related
  PostgreSQL regression196 passed; final selected terminal/projection rerun16
  passed; focused fairness/startup14 passed. Scopes overlap. Retain an earlier
  admission failure before the changed terminal path, its successful isolated
  rerun and successful final selected rerun; no tolerance/guard is relaxed and
  no retrospectively unique fixture predicate is claimed. Hooks pass.
- [x] Complete an operational30-minute control window on deployed revision126:
  351 five-second samples /702 HTTP200s, three profile transitions,3 transient
  stale/fenced samples,19 probe timeout transitions, no ERROR/restart. Native
  diagnostic counters advance between05:54:12 and06:23:31UTC:1,751 ticks,
  234 submissions,135 cancellations and63 checkpoints. This is not proof of
  uninterrupted ticks or a canonical quote-target qualification.
- [x] User-deployed `ae553598a` resolves to revision127's exact
  `sha256:7e6078e2f46d2bc8cebf637504e1a6f11286b19df8bbeca13f0613a01e72d1d1`
  at100% traffic. Env, resources and Cloud probes compare unchanged to126.
  Full v3 observation08:24:36–08:54:37UTC:901 samples,1,802 successful reads,
  one revision/session/control86, three profile changes and3 transient fences
  retained as unknown. Logs contain13 probe timeouts versus19 in the control,
  20 loop-stall/recovery pairs and no ERROR/restart. This descriptive comparison
  is not a controlled A/B speedup or proof of uninterrupted engine ticks.
- [ ] Resolve the remaining probe paths and repeat the unchanged99% quote gate.
  Revision127 Spot is602/668 =90.12%; revision128's fresh window is676/697
  =96.99%, still fail. Perpetual has no confirmed eligible samples under the
  current inventory guards. Synchronous owner/write/COMMIT waits remain.
- [x] Reproduce the missing-terminal-evidence scheduling defects with TDD:
  healthy WS traffic leaves a managed pending cancel/proof on120s, and an event
  arriving during the native account read is discarded at poll completion.
  Eight valid RED cases cover both OKX connectors. Use the existing5s cadence
  only while managed cancel/proof is pending. Consume the wake before awaiting
  I/O; coalesce requests into a serial follow-up and retain the original failed
  poll retry/backoff. No extra worker, terminal shortcut or new env is added.
- [x] PostgreSQL native cancel/fill proof covers zero-fill cancel, partial cancel,
  full fill, missing fill and explicit51603. ACK alone remains pending;
  incomplete evidence keeps the native order active. Complete original fee/amount
  receipts are durable before termination, the physical owner stays unchanged,
  and no replacement POST is sent. Completed orders restore ordinary cadence.
- [x] Ship the new connector tests in the native regression target:3,654 passed
  +16 subtests; related PostgreSQL50 passed and final financial rerun5 passed
  (overlapping scopes). Focused connector/tracker checks56 passed; hooks pass.
- [x] Verify user-deployed `d82cd5c28` against revision128's exact digest at100%
  traffic and unchanged env/resources/probes. The09:24:55–09:54:56UTC window
  retains901 samples /1,802 successful reads, one revision/session/control86
  and three profile handoffs. Two transient fences remain unknown; no sampled
  status.error or persistent HALT. Logs have9 probe timeout transitions,
  14 loop-stall/recovery pairs and no ERROR/restart. This descriptive result
  does not uniquely establish a performance improvement over127's13 timeouts.
  Spot's sampled no-quote timer peaks at5.442s in a guard interval; the98s
  outage does not recur. One SELL pending-cancel sample clears between its
  neighboring observations (3.983s bracket), but has no wait-age/branch trace.
- [ ] Close connected missing-terminal-WS qualification and the99% quote target;
  do not infer exercise of the new5s fallback from ordinary cancellation.
  The defect is reproduced, but the unique cause of the four historical waits
  is not proven: direct venue verification lacked local Demo keys and the
  read-only managed-credential DB connection was unavailable. Retain the98s
  failure and the absence of a venue/worker-queue trace.
  Investigate the revision127 Spot cancel-confirmation gap before
  claiming availability: observed no-quote98.189s / cancel wait91.293s,2 pending
  cancels per side, then2 recorded BUY completions/2 SELL cancels at profile
  transition. A still-live WS with missing terminal evidence can leave the
  shared fallback on120s; this is a hypothesis for these four orders, not a
  proven unique cause. Bound reconciliation without bypassing venue/fill/fee
  evidence, current native state or physical ownership. Retain the failed gap.

## Revision128 gap analysis and managed entry wakeup

- [x] Inspect all14 Spot eligible gaps and7 unknown observations against exact
  probe timestamps. Twelve gaps report `executor_activity` with no pending
  create/cancel; two report `submission_pending`. Most executor gaps are minutes
  from a probe transition. Four unknowns are stale profile-transition views and
  three are startup. Keep all21 failures; this is not a unique per-order trace.
- [x] RED five real PositionExecutor loop cases before the runtime correction:
  managed stop and terminal delivery can leave the outer loop asleep even after
  its inner shutdown waiter wakes. Reuse the existing native order-change event
  for managed configured LIMIT_MAKER entries, retaining the normal periodic
  timeout, financial shutdown, held-fill and fresh-account gates.
- [x] Cover BUY/SELL, zero/partial fills, native terminal delivery during cancel,
  duplicate stops/foreign callbacks and legacy/unconfigured/taker cadence.
  Ship the nine entry cases in the canonical native regression target.
- [x] Full-service offline missing-terminal-WS qualification on the exact
  deployed128 artifact: eight native quotes, two fills hidden from REST, six
  zero-fill cancels and12 dropped terminal frames while account WS stays live.
  Both connectors actually select short polls and enter their native account
  worker twice. Missing fills keep the native orders/levels reserved; release
  restores quotes with two exact original fill/fee receipts and the same owner,
  session and engine PID. This closes local full-service branch coverage, not
  the connected quote target or the historical four-order root cause.
- [x] Native3,674 tests +16 subtests and38 related disposable PostgreSQL tests
  pass. The valid fixture RED retains11 failures against the old simulator.
  Keep the earlier E2E failure caused by test-wrapper initialization separately;
  the corrected deployed-image run passes without altering runtime budgets.
- [x] The packaged entry-wakeup candidate passes the same full-service fault
  scenario at2CPU/1GiB:12 dropped terminal frames, repeated actual5s native
  polling on both connectors, exact fill/fee receipts, reserved pending levels
  and unchanged owner/session/PID. The full-service check is not a latency A/B.
- [ ] Deploy the entry-wakeup change and repeat the unchanged connected30-minute
  target. Revision128's96.99% and
  the earlier98s outage remain failed evidence. Probe/COMMIT/owner-read delays
  remain separate follow-ups; waking an executor is no new trading authority.

Evidence: `build/quote-gap-followup-20261113/` and
`build/e2e-offline/20261113-171748-f20bf3/`,
`build/e2e-offline/20261113-172030-ad7782/`; use the existing
[acceptance record](../../reports/demo/cloudrun-quote-replacement-acceptance.md)
for artifact identities and qualification scope.

Evidence: `build/probe-terminal-investigation-20261113-1253/`. The baseline is
revision126. Connected revision127 evidence is in
`build/terminal-read-postdeploy-20261113/` and the existing acceptance report.
The separate pre-window query retains6 private-WS handshake HTTP502 ERRORs;
the exact observation window has none. Remaining synchronous owner/write/COMMIT
waits are not removed by read isolation; no zero-timeout guarantee is made.
