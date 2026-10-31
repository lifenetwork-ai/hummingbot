# Current-image OKX Demo acceptance

## Current release work — single source of pending actions

Updated 2026-10-31. Candidate image `5973410d` contains the verified runtime
fixes. Diagnostic changes `146826501` / `bf2553679` do not change the application
runtime. The earlier verified bugs remain closed. Only R1 and R2 below are
pending execution; older checkpoints are history, not additional tasks.

- [ ] **R1 — Complete current-image release verification:** freeze the image
  and harness hashes; run warm300/mixed900/burst90/continuous-soak3600 and all six
  recovery faults as one uninterrupted gate, with the validated incident recorder.
  Set its test-only capture count to128 for this long run; retain the same
  512KiB ring and 32MiB stream caps per role. Record/verify the applied policy
  before measurement. The earlier short comparisons used the default32.
  Keep 2 CPUs/2GiB, pool5, TTL15, history, rates and error/latency/retention budgets.
  Preserve any failure and diagnose it before another long repeat. No separate
  passing windows may be combined into a pass. The measured windows alone take
  81.5 minutes, followed by fault verification and cleanup.
- [ ] **R2 — Complete the passing-baseline shipping control:** after R1 passes,
  run ordinary-entrypoint warm300/mixed900 with its matching image, YAML, client,
  exchange fixtures and resource limits. Reconcile ledgers, verify Demo
  preservation, clean temporary resources and publish the scoped release result.

R1 and R2 are validation work, not two unimplemented features. The latest short
shipping/recorded diagnostics pass, but do not satisfy these full acceptance
scopes. A new runtime change is needed only if a confirmed defect justifies it;
do not invent a fix as a prerequisite to validating the frozen candidate. A
failed gate must be investigated rather than retried blindly.

## Historical causes — unresolved evidence

| ID | Observation still unexplained | Current disposition |
| --- | --- | --- |
| H1 | Simulator delivered 498.033/s against the 500/s minimum in one earlier combined run | Bounded diagnostics complete; later bursts exceed 509/s. Cause not established; retries do not explain the failure. |
| H2 | Older sustained supervisor-liveness incident | Original logs lack sufficient probe/operation timing. Not reproduced; do not assign it to the separately fixed controller race. |
| H3 | Five recovered timeout probes over 7.206s and sampled scheduled lag6.723s in the failed current-image burst | Automatic capture and both short comparisons pass. The long episode is not reproduced; no speculative runtime fix. |

These three uncertainties stay explicit. They are not marked fixed, silently
waived or duplicated as executable checkboxes. A green R1/R2 can establish
current scoped release acceptance without proving an old incident's cause;
the historical uncertainty must still be disclosed. Confirmed defects found
during validation require Red/Green remediation before acceptance.

## Completed automatic-capture follow-up

- [x] Red/Green test bounded pre-incident stack/SQL/CPU context, failed-probe and
  recovery triggers, explicit retention/loss limits and failed-baseline exclusion.
  Expanded related native suite: 61 passed, including a Red/Green correction
  that removes unnecessary source-text lookup/caching during stack sampling.
- [x] Verify automatic capture with a known short engine pause on the isolated
  image, keeping actual injection timestamps and retained prehistory/recovery.
  Ninety-second packaged diagnostic passes: 90 matching fills, no HTTP errors,
  p99 129.48ms, same engine PID; both roles retain failure/recovery and context.
- [x] Compare one shipping-entrypoint warm300/mixed900/burst90 diagnostic with
  one matched incident-recorded sequence. Keep the same image/YAML/client/
  exchange fixtures, caps, history size and budgets; retain every failure.
  Both pass all three windows: shipping p99 274/173/264ms; recorded 92/145/166ms.
  Each reconciles 1,290 fills across all four families, zero errors and stable
  engine/API PIDs. These are diagnostics; the baseline's release failure stays.

Bounded follow-up complete: the historical long-lag event is not reproduced,
so H3 and R1/R2 remain unresolved/pending respectively. No speculative runtime
fix or threshold waiver is made. Capture limits and new short observer/DB-wait
observations are explicit in the report.

See [incident investigation](../../reports/mm-incident-investigation.md).

## Historical verification checkpoints

The following journal preserves each image's actual result. Failed or
unaccepted outcomes remain labelled as such; their next actions are tracked
only once in R1/R2 and H1–H3 above. Do not rerun superseded images to complete
an old checkpoint, or treat a checked diagnostic task as release acceptance.

Operator request: 2026-10-30. Selected runtime `715a11f3…`, after completed local
regression/E2E/load/hour/recovery/control acceptance. Earlier real Demo results
retain their original image scope.

Use the existing dedicated Demo account, PostgreSQL history, controller IDs,
private environment/catalogue and bound policies. Preserve existing long/short
positions. The original zero-fill protocol performed no HALT reset, forced fills,
live routing or Cloud Run deployment. The later operator-approved two-row repair
and explicit resolved-HALT clear are documented in the follow-up below.
Keep Start/Switch/Stop on the admin API; YAML remains the policy authority.

- [x] Read-only preflight: dedicated credential validation, same account, Demo
  REST and public/private WS, active target/reference instruments, balances,
  pending orders, positions and durable recovery/command state.
- [x] Validate the current catalogue and ten runtime fingerprints, back up the
  persistent DB, replace the stopped old container with the accepted runtime,
  and prove existing order/fill/position rows are unchanged across startup.
- [x] Spot: exercise `follow_market`, `sideways`, `follow_token` with independent
  exchange order/recorder checks, post-only/rules/budget validation and Stop.
  Each runs through a 65-second observation. Stop leaves zero pending quotes
  and preserves the existing perpetual quantities. Initial order snapshots may
  be partial while levels are being created/refreshed; no full-book count claim.
- [x] Spot Random: observe a real configured 600-second step boundary, restart
  during the session and verify the pinned session/anchors/deadline, then Stop.
  Step0 follows the market; step1 follows AVAX. Session/profile/generation,
  anchors and global/step deadlines survive restart; quote recovery and Stop pass.
- [x] Repeat the three fixed profiles for perpetual and both markets while
  preserving existing exposure and all admission/risk gates.
- [x] Reconcile final pending orders, newly observed fills/original fees and
  position quantities against PostgreSQL. Explicitly report zero-fill coverage
  if the market produces no natural fills; do not invent positive fill evidence.
  All 9 profile/mode cases pass. Independent terminal reads confirm 404 canceled
  post-only orders with exact original price/base quantity and both sides in
  every case. One failed local intent has no exchange order. New fills/fees are
  zero, so positive new-fill fee processing remains unobserved on this image.
- [x] Leave trading stopped with no test-owned pending quotes, preserve private
  files/DB/backup, audit logs, publish sanitized scoped evidence and commit.
  Service remains available with trading stopped, owner healthy, no HALT or
  pending mutation/command. Eight retry ERROR records are classified as guard
  rejections and all eight attempted client IDs are absent on OKX. See the
  [report](../../reports/okx-demo-current-image.md) and
  [sanitized evidence](../../reports/okx-demo-current-image-evidence.json).

Raw helper/results: ignored `build/okx-demo-current/`. The public evidence must
contain no API keys, passphrases, DB URL or admin password.

## Positive natural-fill follow-up — operator request 2026-10-30

The completed zero-fill protocol above retains its scope. This follow-up uses
the same accepted runtime/DB/account and a temporary private Demo catalogue:
40 USDT per market, one level per side, base spread0.0003 before the unchanged
maker-fee floor and NATR multiplier. All guard/exposure/initial-position policies
and controller IDs remain unchanged. Preserve and restore the canonical service
and original catalogue. Maximum observation1200s; fills are never forced.

- [x] Refresh read-only account/WS/DB preflight, compare existing position units,
  validate the temporary catalogue and take a private DB backup.
- [x] Observe at least one new natural maker fill in spot and perpetual; compare
  exchange trade IDs, exact base quantity/price and original fee currency/amount.
  Six natural maker fills match independent OKX reads: one spot and five perpetual.
  Spot fee-net BTC cash and perpetual baseline plus OPEN fills match exactly.
- [x] Verify unique recorder identities through repeated independent readback
  and filled-exposure restart; preserve session/anchors/deadline and reconcile
  held position quantities after Stop.
  Fixed imagee526 passes real active restart and resumes both markets. All six
  original fill rows remain byte-equivalent and unique; no additional fills occur
  during this retry. Exact quantity/cost/fees and venue exposure match after Stop.
- [x] Stop/cancel all observation quotes, restore the canonical service/profile
  stopped, preserve fill/executor history, venue positions and private files,
  and publish sanitized evidence.
  Canonical API now uses fixed imagee526, original catalogue restored, stopped
  without HALT or pending mutations/orders. Private files and all four ledger/
  position hashes are unchanged across the final startup after reviewed repair.

Positive fill and real filled-restart acceptance are complete across the scoped
original observation and fixed-image retry. Neither run forces exchange fills.

### Filled-restart defect remediation

Original image715a fails this new restart case: managed YAML `initial_positions`
overrides post-fill durable holds, then failed initialization stores stale seed
aggregates. Fill history survives. After the approved repair and passing fixed
retry, canonical API is up with trading stopped and no HALT. See [follow-up report](../../reports/okx-demo-natural-fill.md)
and [scoped evidence](../../reports/okx-demo-natural-fill-evidence.json).

- [x] Reproduce stale seed overwrite and failed cleanup using Red tests; verify
  a managed bootstrap-only fix and preserve upstream unmanaged override behavior.
- [x] Independently compare all six exact quantities/prices/original fees, venue
  maker status, spot cash, perpetual exposure and three unique-ledger readbacks.
- [x] Reconstruct two aggregate rows from the pre-test snapshot and matched fills;
  validate conditional repair on a disposable post-failure clone, preserving
  fills and HALT and rejecting stale repair replay before actual maintenance.
- [x] Complete broader native/PostgreSQL and replacement-image E2E verification,
  including seeded positions followed by fills and active restart.
  Native2,047 + 11subtests, PostgreSQL268 and targeted image E2E pass. Original
  image fails the same seeded test. Fixed imagee526118b…/manifest566788f2… is built
  from runtime commit783d35c69. This does not claim a new full release/hour gate.
- [x] Obtain explicit operator review before actual Demo aggregate maintenance
  and clearing only this resolved HALT; no automatic reset or history deletion.
  Operator approved both preset and reviewed repair/retry. Fresh backup/readback,
  stopped writers and conditional two-row update preserve all original history.
- [x] Repeat real filled-exposure recovery with the fixed image, independently
  reconcile final quantities/fees/dedup and restore stopped canonical service.
  Real restart resumes four post-only quotes with pinned session state; exact
  held quantity/cost/fees/volume and three unique-fill reads pass. Final API is up,
  original catalogue restored, stopped/no HALT/no pending quotes or mutations.

## Full release revalidation of fixed imagee526 — operator request 2026-10-30

This is a new acceptance scope, after the completed real Demo follow-up above.
Pin image `sha256:e526118b94324ab4a116839c171482fffa4c3e66adc5effa626d2c0242172454`
and manifest `sha256:566788f22f8c86f1a7df38088bf23557baf7d1c16cff2c4b411ed2f64e4cb918`.
Use isolated fake-exchange/PostgreSQL fixtures; leave persistent Demo stopped and
its order/fill/position history unchanged. Preserve all failures and unchanged
budgets. No live trading, registry publication outside loopback, or cloud deploy.

- [x] Verify immutable image/runtime fingerprints, unchanged budget/runner source,
  host power/lid readiness, stopped Demo baseline and native/PostgreSQL regression.
  Native2,047 + 11subtests and PostgreSQL268 pass. Ten packaged runtime module
  hashes match source. The pinned manifest is cached locally; Demo baseline hashes
  and original stopped intent are recorded for final comparison.
- [x] Complete packaged runtime/ABI/GC/Swagger checks and all seventeen E2E groups,
  including seeded filled restart and compatible image upgrade/rollback.
  All17 pass at `build/e2e-offline/20261030-141530-9aeb28/result.json`, including
  distinct previous-image upgrade/rollback and retained economics.
- [x] Verify ordinary shipping supervisor/API/engine crash/hang recovery and reaping.
  The actual entrypoint passes API repeated crash/hang, engine crash/hang,
  retained HALT, process reaping and ownership release at
  `build/service-supervision/20261030-145208-f3d592/result.json`.
- **Historical outcome — e526 gate not accepted; superseded by R1:**
  The requested all-feature load scope was warm300, mixed900, burst90,
  continuous soak3600 and all six recovery faults; 2 CPUs/2GiB, pool5 and TTL15.
  First attempt `20261030-145524-a2b39f` fails soak p99: primary1086.76ms,
  auxiliary1137.27ms; zero HTTP errors. Warm/mixed/burst pass. Independent six
  fault cases pass at `20261030-161919-6d9c57`; these do not turn the failed full
  attempt into a pass. Repeat `20261030-162807-3036fa` passes the latency budgets
  but fails zero-error acceptance: one daily reporting query hits PostgreSQL's
  2s statement timeout, causing 15 explicitly stale responses over4.2s. Preserve
  both failures and diagnose without weakening the healthy gate.
- [x] Validate complete per-role minute samples, stable PIDs and bounded
  RSS/thread/descriptor/queue growth, three-profile coverage and exact fill/fee/
  position/funding ledgers. Keep the existing zero-error and p95/p99 budgets.
  The failed first attempt nonetheless passes these specific retention/ledger
  checks: 57 samples, max gap66.35s, stable role PIDs, 4,890 matching fills,
  all three profiles and no retention violation. Its p99 violations remain open.
- **Historical outcome — e526 control not accepted; superseded by R2:**
  No passing-baseline matched ordinary-entrypoint control was established. A diagnostic
  from the failed baseline also fails mixed p99/backlog; it does not establish
  acceptance for that checkpoint.
- [x] Recheck persistent Demo preservation, clean temporary resources, publish
  scoped portable results, check only achieved items and commit. A failed gate
  remains open until a justified fix and an unchanged repeat pass.

### Operator priority: slow reporting and trading availability

On 2026-10-30 the operator clarified that a slow aggregate is acceptable if the
MM engine is unaffected. Preserve the historical strict zero-degradation failure;
evaluate this operational priority separately, without presenting stale data as
fresh or claiming an absolute independence guarantee for shared CPU/PostgreSQL.

- [x] Inject one actual 2s PostgreSQL statement timeout into the API's daily fee
  read on the isolated shipping image; verify stale observations are clearly
  labelled, retain the original observation time, remain under60s old, suggest a
  bounded retry and later become fresh. No unrelated HTTP/semantic errors.
- [x] Independently observe at least one new fill durably committed to PostgreSQL
  while the reporting query is still blocked; verify exchange/recorder/position
  agreement, continuing market updates/fills, same owner/session/engine PID,
  no HALT and clean Stop. Record the observer's overhead and test limitations.
- [x] Repeat the controlled timeout with a CPU-heavy fee-query expansion on the
  same constrained DB; measure engine progress and report separately from the
  wait-only case. A single synthetic test cannot establish universal isolation.
- [x] Report both the strict release result and the operator-priority evidence,
  including that a server-side wait does not establish isolation from CPU-heavy
  SQL or all future Cloud Run workloads.

### Engine liveness follow-up — 2026-10-30

- [x] Reconstruct the retained restart/lag timeline without treating sampled stacks
  or correlated host activity as a proven root cause. The original restart was
  126.698s after the daily fallback and 9.877s after an engine cancellation callback.
- [x] Add bounded supervisor failure/recovery diagnostics with Red/Green tests:
  distinguish timeout/HTTP status/connection failure, failed-probe count, elapsed
  failure time and sampling gaps. Preserve the 300ms timeout and existing grace.
- [x] Exercise real loopback HTTP503/timeout and isolated source-overlay wait
  diagnostics; retain every failed attempt separately. Overlay runs are not
  acceptance of the original e526 image or a newly packaged release.
- [x] Repeat the CPU-fault diagnostic after the operator reopened the Mac,
  preserving thresholds and all prior failures. Run `20261030-210016-db909f`
  passes: 120 matching fills, 99.98318 simulator updates/s, p99 API 89.70ms, no HALT
  or engine replacement. Two transient probe timeouts recover. The recorded
  clamshell sleep began after the prior tests, so it does not explain their failures.
- **Historical unknown H2:** The original sustained-failure/long-lag cause lacks
  sufficient evidence. Short transient timeout recovery neither identifies its
  cause nor satisfies the failed release gates above. See
  [investigation](../../reports/mm-engine-liveness-investigation.md).

### Packaged supervisor-diagnostics release — operator request 2026-10-30

Build source commit `e9a1645b7` into a new immutable image. No source overlays,
no real-exchange actions and no replacement of the stopped persistent Demo.
Preserve historical failures. Report strict release acceptance separately from
the operator's tolerance of slow/explicitly stale aggregate reporting.

- [x] Build the immutable image; verify packaged supervision source hashes and
  capture host power/lid state and Demo preservation baseline. Image `dd602fb2`
  is built; registry manifest pinning is part of E2E preparation.
- [x] Complete packaged runtime smoke and offline E2E, including prior-image
  upgrade/rollback, on the newly built artifact. All 17 groups pass at
  `build/e2e-offline/20261030-212044-749aa2/result.json`.
- [x] Exercise shipping supervisor/API/engine crash/hang recovery on that image.
  Passes at `build/service-supervision/20261030-215710-5520a1/result.json`.
- [x] Run warm300/mixed900/burst90/soak3600 and all six fault cases with unchanged
  rates, resource caps, pool5, TTL15 and error/latency/retention budgets. The
  existing `--keep-going` option may retain later windows after budget failures;
  it must still return a failed overall result when any window fails. Run
  `20261030-215951-3d41e4` completed: first three windows and all six faults pass;
  soak latency passes but synthetic fill-generator drain fails (10.153s, two
  fills short). All 4,888 emitted fills and positions reconcile. Keep this
  failed result, including the scheduled Random handover/quote-gap evidence.
- [x] Diagnose the end-of-window handover/quote gap and repeat strict
  zero-error/backlog soak on corrected packaged image `81fa1448`. The 60-minute
  soak passes without shortened windows or relaxed limits; the original
  `dd602fb2` run remains failed. The separate combined-run burst failure and
  targeted retry are recorded below.
- [x] Run matched ordinary-entrypoint warm300/mixed900 control. If the baseline
  fails, any additional control run is explicitly diagnostic and cannot close
  strict acceptance. Diagnostic control `production-control-20261030-232929-970ba2`
  passes: p99 219.93/240.13ms, zero errors, 1,200 matching fills and positions.
- [x] Reconcile ledgers, review probe episodes and continuity, retain every
  result, verify Demo preservation, clean temporary resources and commit report.
  All 64 transient probe episodes recover; no measured-window process restart.
  Preserve the failed original Random soak. The historical sustained-liveness
  cause remains open; the corrected image was tested separately below.

Progress: [packaged release report](../../reports/probe-release-20261030.md).

### Random handover investigation — operator request 2026-10-31

**Bug status: closed — verified, 2026-10-31.** Fix `056b11495` stops
post-window fill generation; Red/Green regressions and the final-image soak
verify the correction. This closure does not waive any failed release window.

- [x] Reproduce the producer lifecycle defect before changing it: an empty book
  at window end is incorrectly treated as undrained work, and a lookup returning
  after window end can dispatch a new fill. Two failing regressions, then all
  four lifecycle cases and the existing budget/exchange tests pass (31 tests).
  End new generation with the measurement window; still await/count dispatched
  fills and fail genuinely stuck requests under the original ten-second limit.
  Expose shortfall rather than manufacturing fills; fixed-run rate gates remain.
- [x] Observe short Random transitions on the same packaged image, record quote
  guard/refresh decisions and reconcile the independent ledger. Both 180-second
  diagnostics pass (`20261031-043633-11a09f`, `20261031-044129-a1fcc9`): 174 fills
  each, six-fill shortfall visible, zero errors, drains 0.029/0.102s. The first
  observes `inconsistent_own_depth` safety cancellation; the second observes
  stable refresh checks. Native guard/defense/client regression: 107 passed.
- [x] Repeat full warm/mixed/burst/sixty-minute Random soak and fault verification
  with the corrected client. Final image `81fa1448`: soak and six faults pass;
  combined run fails burst feed rate, retained in the report. A separate burst
  repeat passes with unchanged thresholds; do not relabel the combined run.
- [x] Update evidence and checklist, verify Demo preservation and clean test
  resources. The earlier unexplained sustained-liveness event remains open; a
  passing unrelated run does not identify its historical cause.

Evidence: [Random handover investigation](../../reports/mm-random-handover.md).

### Controller scheduling follow-up — 2026-10-31

**Bug status: closed — verified, 2026-10-31.** Fix `3789b2500` prevents
the reproduced observer/controller scheduling race from latching a false HALT.
Red/Green tests, old/new image fault reproduction and the final-image soak pass.

The corrected-client repeat `20261031-044542-876453` reached a separate runtime
HALT (`controller_task_stalled`) near soak minute 50. Preserve that failed run;
do not describe it as a successful Random/release gate or an OKX outage.

- [x] Reproduce the observer/ready-controller ordering race with Red/Green
  tests. A delayed ready callback and an already-expired timer must get their
  waiting controller scheduled before a persistent HALT decision. Keep the
  synchronous send gate closed until actual progress; a genuinely stuck task,
  explicit task failure and shutdown cancellation remain covered.
- [x] Verify the correction on immutable shipping images with a controlled
  engine scheduling pause, retaining both old/new results. This fault experiment
  does not prove the precise cause of the older host's scheduling delay. Old
  `dd602fb2` HALTs on the first 6.529s pause; new `dbd5b5ba` passes all three
  pauses with unchanged PID/session and eight reconciled fills. See
  `build/controller-stall/20261031-061636-c97288/result.json`.
- [x] Run packaged regression and unchanged full load/retention gates on final
  image `81fa1448`. Soak and six faults pass; the combined run fails burst feed
  rate and an independent unchanged burst repeat passes. Preserve both results.


### OKX lost-order status follow-up — 2026-10-31

**Bug status: closed — verified, 2026-10-31.** Fix `fc0ee55b8` handles
explicit order absence through the existing spot/perpetual lost-order lifecycle.
Re-enabled upstream regressions and the final-image validation verify the fix.

- [x] Re-enable upstream spot/perpetual lost-order absence tests and observe both
  fail. Decode explicit OKX 51603 into the existing tracker's absence path;
  keep ambiguous payloads and network failures retryable. Related Green suite:
  126 tests and two subtests. Preserve fees/fill history and clock-error handling.
- [x] Verify immutable image `81fa1448`: native/PostgreSQL/E2E, supervision,
  controller scheduling experiment and unchanged load gates. Combined run fails
  burst feed rate; targeted burst repeat passes independently. Prior `dbd5b5ba`
  mixed failure and interrupted partial soak remain preserved.

### Earlier unresolved outcomes — tracked once in the current register

The operator requested closure of the three verified bugs above. The following
items remain separate; neither a historical failed run nor its cause is erased.

- **Historical unknown H1:** Simulator burst-rate shortfall (498.033/s versus
  the unchanged 500/s minimum). The independent 509.80/s retry passes but does
  not explain the failed combined run.
- **Historical unknown H2:** The older sustained supervisor-liveness cause is
  not established. Do not equate it with the reproduced controller race
  or infer its cause from a later passing run.
- **Unaccepted checkpoint — next validation is R1:**
  Candidate `5973410d` passes warm/mixed and burst feed/fill rates, but fails
  burst p95/p99. Its partial soak is not accepted; the separate passing traced
  warm/mixed/burst diagnostic does not close this gate. Earlier `81fa1448` soak
  and six-fault results remain historical evidence.

### Historical execution protocol — 2026-10-31

Historical protocol: `81fa1448` was the then-current candidate and was later
restored as unchanged-runtime image `5973410d`. The execution checklist now
lives in R1/R2 above; this protocol explains the original diagnostic decisions.
Keep old outcomes unchanged and do not repeat superseded-image gates. These
investigations do not reopen the three verified bugs above.

#### 1. Explain the burst generator shortfall with short diagnostics

The immediate question is why the simulator delivered 498.033/s in the combined
run and 509.80/s in a separate retry. Neither result establishes a bot defect.

1. Reuse `Exchange.market_updates()`, `/market-metrics` and
   `measure_market_burst()`; add bounded diagnostic measurements only where
   existing evidence is missing. Retain actual sends per one-second interval,
   elapsed monotonic time, scheduled-versus-actual emission lateness, active
   book subscriptions, send duration, and producer/container CPU or throttling
   where available. Always retain the measurement on assertion failure too.
   Do not log order/account payloads or add an unbounded event history.
2. Use Red/Green tests for diagnostic behavior: actual-send counters, failed
   sends, sample limits, monotonic timing, failed-measurement evidence, and
   restoring the steady feed in `finally`. Never increment counters for work
   that was not sent. Keep the existing 500/s minimum and counting tolerance.
3. Run a small matched set sequentially: an unchanged uninstrumented burst,
   an instrumented burst, and one targeted confirmation if the first two expose
   a hypothesis. Keep image, fixture, resource limits and admin workload fixed;
   compare the retained pre-burst order/subscription state with the fresh retry
   and reconstruct any relevant difference for the diagnostic. A fresh fixture
   passing is not a matched reproduction of the combined run's warmed state.
   Record emulation/host conditions without assigning them as causes. No build
   or other benchmark runs concurrently. Target a 10–15-minute diagnostic pass;
   if inconclusive, record the missing evidence rather than repeating blindly.
4. If a generator defect is demonstrated, write the failing regression first
   and fix the generator. If runtime backpressure is demonstrated, fix the
   affected runtime path with Red/Green tests and rebuild the candidate. A
   larger headroom multiplier or a higher timeout is not a causal fix.

Closure requires evidence explaining the shortfall and verification of any
correction under the unchanged threshold. A passing retry alone is evidence of
capacity in that run, not an explanation of the failed one. Preserve an
inconclusive outcome as open; do not force an engine change.

#### 2. Bound the historical supervisor investigation

This has greater availability impact, but the old logs lack probe details that
cannot be reconstructed. Perform a focused review alongside the burst analysis,
without running competing workloads during a timing measurement.

1. Reuse the supervisor reason/status/timing logs, controller stage/progress,
   process identity, loop-lag and SQL/recorder timing already implemented.
   Build one timeline of the original event and list which facts are missing.
2. Select a short controlled fault only when a specific hypothesis is testable:
   distinguish transport timeout, explicit unhealthy response, observer stall,
   engine scheduling delay and shared-DB delay. Use the existing shipping-image
   fault harness and ownership/recovery assertions; do not add another service.
3. For a confirmed defect, require Red/Green tests and a targeted shipping-image
   check. Preserve order fencing, retained HALT when outcomes are uncertain,
   and existing restart/probe limits. The closed controller scheduling race is
   not automatically the cause of this separate sustained failure.

Closure of the historical cause requires supporting evidence. If the focused
review cannot establish it, record `not reproduced / insufficient historical
evidence`, retain the open investigation and identify the exact evidence to
collect on recurrence. Do not spend repeated hour-long runs trying to recover
missing historical logs. Release testing and causal closure remain distinct.

#### 3. Revalidate the release candidate once the short work is complete

1. Freeze the candidate image and harness hashes. If code changed, run affected
   regression/packaged checks first; do not rerun unrelated suites solely for a
   documentation change. Report any diagnostic or harness changes explicitly.
2. Run the unchanged full gate sequentially: warm300, mixed900, burst90,
   continuous soak3600 and all six recovery faults; 2 CPUs/2GiB, pool5, TTL15,
   the same history fixture and error/latency/retention budgets. Allow about
   90 minutes. Separate passing windows must not be stitched into a passing run.
3. Once that baseline passes, run the matched ordinary shipping-entrypoint
   warm300/mixed900 control using its image, fixtures and resource limits. This
   addresses the remaining control acceptance on the current candidate without
   retroactively accepting the old image's failed control.
4. Reconcile actual orders/fills/fees/positions; review PID continuity, brief
   readiness waits and per-process memory retention. Verify Demo preservation,
   clean only test resources, update evidence/checklists and commit.

A fully green gate closes current release acceptance. It does not identify the
old supervisor cause; any remaining uncertainty stays explicit for the operator.
If a gate fails, keep the result and investigate that failure before another
long repeat. No automatic deployment or real-exchange trading is part of this
plan.


Execution checkpoint: diagnostic commit `44c82ecd8` passes 36 related native
checks after five Red tests. Restored image `5973410d` retains the previously
verified application source; runtime smoke passes. Three short cases complete:
all deliver more than 509/s; the first fails p99, the other two pass. A preload
of 307 known canceled orders does not reproduce the burst shortfall. Historical
burst and supervisor causes remain open; the bounded diagnostic stage is
complete, with no new runtime defect established. See the
[follow-up report](../../reports/mm-burst-followup.md). The later current-candidate attempt fails burst latency as recorded below;
matched ordinary-entrypoint acceptance control is not run from that baseline.


### Load-client family selection — closed, verified 2026-10-31

- [x] Reproduce head-of-line blocking: a continuously executable spot quote
  generates only one fill in two seconds because the old client waits for a
  missing perpetual BUY. Four original generation/drain controls still pass Red.
- [x] Rotate among actual executable families and skip exhausted levels; retain
  original deadlines/rate/amounts and report per-family fill counts. Fix
  `48d2967d0`; 40 related tests pass, including absence, recovery and fairness.
- [x] Verify packaged 300-second diagnostic: 300/300 fills, all four families,
  exact recorder/position agreement. Corrected full attempt then passes warm300
  and mixed900 at 300/900 fills. This test-client bug is closed; it is distinct
  from runtime controller/OKX bugs and the still-open latency issue.

### Diagnostic checkpoint before automatic incident capture — 2026-10-31

- [x] Complete bounded generator diagnostics and focused historical review;
  preserve the failed baseline p99 case and all passing controls. Add failed
  measurement retention and bounded scheduling/send/CPU diagnostics (`44c82ecd8`).
- [x] Attempt the current full gate with the corrected harness. Preserve
  `20261031-151026-01897f`: warm and mixed pass; burst feed 509.80/s and all
  90 fills pass, but primary/auxiliary p95/p99 fail (primary p99 4,622.38ms).
  The attempt stops during partial soak for focused diagnosis; no hour/fault
  acceptance is inferred from the incomplete attempt.
- [x] Run a fresh traced burst, then one traced warm300/mixed900/burst90 sequence
  to compare warmed conditions (`20261031-154316-871c04`). All three traced
  windows pass; source/fixture hashes match the failed attempt. Trace and the
  intentional stop-before-soak diagnostic wrapper are explicit differences.
  This is a diagnostic only, not a stitched full-release pass.
- **Historical unknown H3:** Recovered engine probe episode in the failed burst:
  five timeout probes over 7.206s, sampled scheduled lag 6.723s, no replacement.
  Shared bot-cgroup throttling overlaps it, but full causal attribution is
  missing. The matched traced burst's largest captured stall is only 290ms.
  Historical burst-rate and old sustained-liveness causes remain open too.
- **Unaccepted checkpoint — next validation is R1/R2:**
  No ordinary-entrypoint acceptance control is run from this failed
  baseline. Continue only with evidence targeting the specific latency episode;
  do not repeat hour gates blindly or relax limits to force closure.
- [x] Verify unchanged Demo image/control/four ledger fingerprints and clean
  temporary test containers. Publish the outcomes and remaining limits in the
  [follow-up report](../../reports/mm-burst-followup.md).
