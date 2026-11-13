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
- [ ] Deploy and compare a new bounded window with the same300ms probe limit.
  Keep import/send-path waits and quote-target failures separate. Removing these
  avoidable SQL reads is not a guarantee that all future probes meet300ms.

Evidence: `build/probe-sql-followup-20261113/`. This change remains local; Cloud
bot state, risk settings, shutdown budget and probe policy are unchanged.
