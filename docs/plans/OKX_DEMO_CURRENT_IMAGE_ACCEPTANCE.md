# Current-image OKX Demo acceptance

**Current local API candidate:** `086454de` from `0f4095671` reserves one
operations and one heavy slot in the existing two-thread executor. Native2,494
+11 subtests, PG355, Swagger, three E2E groups, four controlled report faults,
300s probe and the unchanged standalone hour pass. Hour primary/auxiliary p99
is246.24/88.55ms,79,200 HTTP200 replies have no errors/stale,3,595 synthetic
fills/fees/positions match and retention grows25.96MiB. Stopped Demo restoration
preserves23 existing fills and four ledger hashes. Preparation has one owner-state
FOR UPDATE timeout and automatic engine restart before the timed window; its
exclusive cause remains unproven. No new full R1/R2 or active natural-fill
acceptance is transferred. [Report](../../reports/performance/mm-admin-worker-lanes.md).

**Preceding retry candidate:** `f4d90bc7` from `d2cdf2a36` retries one
acknowledged daily-read timeout through the existing bounded executor. Native
2,464 +11 subtests, PostgreSQL354, three packaged E2E groups and four controlled
reporting faults pass. Its unchanged standalone hour passes:79,200 HTTP200
responses, no errors/stale, primary/auxiliary p99 615.65/565.42ms and3,594 exact
synthetic fills/fees/positions. The earlier300s probe still fails p99 at
1,094.27/1,112.11ms. A separate traced diagnostic passes823.21/734.17ms and
confirms worker queueing; the original exclusive cause is unproven and overall
qualification stays incomplete. Stopped Demo upgrade/restart preserves the
ledger, but active natural-fill acceptance below remains scoped to50f96001.

**Preceding reporting candidate:** `50f96001` from `14feb47dd` has source,
three packaged E2E groups, two controlled slow-report faults and positive Demo
spot/perpetual fills with active restart verified. The rich600s probe fails its
reporting/latency gate; the standalone hour fails only zero-degraded reporting
(two original-fee SQL timeouts,28 stale replies), while3,594 fills/fees/positions
and retention checks pass. See the
[scoped follow-up](../../reports/performance/mm-daily-conversion-followup.md).
This does not transfer an older image's full R1/R2 acceptance.

**Earlier accepted quote-recovery candidate:** [quote replacement follow-up](../../reports/investigations/mm-quote-recovery.md)
fixes the terminal-confirmed executor wait on `660bd38b`. Broad source checks,
three packaged E2E groups and matched180s diagnostics pass, followed by
[full R1 and matched shipping R2](../../reports/release/mm-quote-recovery-release.md).
Protective quote gaps, residual probes and the production coverage SLO remain
explicit. The prior accepted `95cde31c`
artifact was subsequently deleted by the operator; its historical gates below
are not transferred to rebuilt or modified images.

**New connected scope on660bd38b:** [Demo verification](../../reports/demo/okx-demo-quote-recovery.md)
passes nine fixed cases, actual600s Random rollover, active spot/both restart
and407 independently confirmed canceled orders. A subsequent bounded approved
preset verifies five natural maker fills (one spot/four perpetual), exact fees,
deduplication and active filled restart on this same image. Original six fills
remain unchanged; final11 rows are unique. Canonical service is restored with
original200/400 catalogue, trading stopped, no pending orders/mutations/HALT.
No live account or deployment is included.

**Preceding local logic fixes:** [runtime audit](../../reports/investigations/mm-runtime-logic-audit.md)
closes durable-HALT cleanup and equivalent-fee replay defects on `95cde31c`,
with broad source and targeted simulated lifecycle/recovery checks. Its separate
[full R1 and matched R2 rerun](../../reports/release/mm-runtime-logic-release.md)
now passes, including the 60-minute soak. Recovered probes remain explicit;
there is no new connected Demo acceptance or upgrade. Earlier images and
completed Demo checklists below predate these fixes.

Preceding local image acceptance: [`e2d393b1` status-publication follow-up](../../reports/investigations/mm-engine-status-publication.md).
Native/PostgreSQL/Swagger, three E2E groups, full R1 and matched shipping R2 pass.
Transient probes remain; there is no new-image connected Demo session or upgrade.
The preceding image `39198499` has a separate [read-only Demo preflight](../../reports/investigations/mm-engine-probe-controlled.md#d--demo-read-only-preflight)
which passes on that earlier image; it has no new active-session acceptance or service upgrade.

Earlier connected trading/upgrade scope: [`a190b024` release and stopped Demo upgrade](../../reports/release/mm-submission-log-release.md).
That service was left trading-stopped at verification time. Subsequent Docker
cleanup removed the old image/container; the persistent Demo DB volume remains.
Historical connected and natural-fill checklists below retain their original
image/date scope; the new local gate does not relabel them.

## Controller/persistence image verification — 2026-11-04

- [x] Build image `f1a7eead` from `4a38bbf09`, verify changed source hashes, and
  perform read-only REST/WS and database/account preflight.
- [x] Run the 600-second instrumented probe and matched 300-second old/new
  comparison. The first probe **fails p99**; preserve this outcome and report
  bounded MM/probe evidence without claiming an established cause.
- [x] Run nine connected Demo profile/mode cases, Random step, active spot/both
  restart, Stop and independent order/account/position checks.
- [x] Independently match three natural perpetual fills and restore the original
  stopped service/configuration after the approved bounded maker preset.
- [ ] Obtain a current-image natural **spot** fill and verify its quantity/fee;
  filled-active restart across both markets is not yet proven by this run.
- [x] Run the standalone 60-minute same-image soak, identify the degraded
  reporting cause, and independently verify restored account/DB state. The soak
  **fails** its zero-degraded-response gate: one two-second PostgreSQL aggregate
  timeout produces 13 explicitly stale daily responses. It is not a full release.
- [ ] Obtain a clean 60-minute zero-degraded-response run if the unchanged gate
  is required; investigate a bounded reporting-specific approach before tuning.
- [ ] Investigate the retained p99 episode and repeated Demo guard waits; do not
  weaken the guards or assert uninterrupted quoting based on sampled data.

[Current report](../../reports/release/mm-controller-persistence-release.md).

### Reporting/probe/guard follow-up — requested 2026-11-04

- [x] Reproduce repeated original-fee decoding in the timed-out fill aggregate:
  PostgreSQL executes 600 fee-array parses for 200 zero-conversion fills.
- [x] Compute conversion counts once in the existing daily aggregate and verify
  200 parses with exact fee semantics, including foreign quote assets/missing fees.
- [x] Compare before/after on constrained PostgreSQL with 100k/1M repeated and
  diverse fees; reject candidates that regress the declared workloads.
- [x] Independently reproduce AVAX freshness loss on the actual Demo `books`
  channel: maximum receive gap 10.96s during a 180s read-only observation.
  This is not attribution of every historical guard wait.
- [x] Run final-source native2,442 +11 subtests and isolated PostgreSQL345;
  preserve the initial fee-query timeout during a concurrent build.
- [x] Build image `50f96001` from source `14feb47dd`, verify seven runtime file
  hashes and the same197 Python package versions as the preceding image.
- [x] Add Red/Green bounded terminal-before-ACK test admission and batch-specific
  persistence proof:65 related native checks (eleven new) and seven new PG cases.
  Preserve the first packaged failure and a passing unmodified recovery repeat.
- [x] Complete all three packaged E2E groups with the final bounded fault fixture.
  Run the rich600s MM/probe diagnostic and preserve its failed reporting/latency
  verdict:13 degraded daily replies, exact600-fill ledger, recovered probes.
- [x] Verify controlled reporting wait/CPU timeouts independently: each has a
  durable fill during the fault, unchanged engine/ownership/session, no HALT,
  later fresh statistics and exact120-fill ledger agreement.
- [x] Observe reference/guard state during the bounded Demo run with an armed
  read-only witness. No new own-depth trigger is captured; no guard change or
  historical causal closure is claimed.
- [x] Obtain one current-image natural spot and one perpetual maker fill, match
  individual REST trade IDs/quantity/original fees and verify active filled
  restart. Preserve21 prior rows; final23 are unique. Independently confirm
  all37 orders terminal and restore the original stopped catalogue/container.
- [x] Run the unchanged standalone60-minute gate and preserve its failed verdict:
  two original-currency fee aggregate timeouts produce28 stale replies. All3,594
  fills/fees/positions match; latency and25.69MiB retention growth pass. Verify
  backup hashes and post-soak read-only preflight, restore stopped/no HALT/pending
  state, publish scoped evidence and commit. Full R1/R2 is not rerun here.
- [x] Obtain a later current-image zero-degraded-response hour onf4d90bc7 through
  the bounded-timeout follow-up below. Its79,200 responses have no errors/stale;
  the50f96001 hour above remains failed. No deadline/latency budget is relaxed,
  no stale reply relabelled, and no general SQL-cost improvement is inferred.

Historical p99/probe and own-depth attribution remain unproven; the bounded
investigation above is complete. Preserve the evidence and capture a recurrence
before proposing a runtime/guard fix. Continuous quote coverage and Cloud Run
capacity are not established by these local observations.

### Original-currency reporting timeout recovery — requested 2026-11-04

- [x] Retain the exact query and failed-image results as the baseline. Measure
  repeated/diverse workloads before accepting an optimization. Reject payload
  grouping, casts/projections, increased local sort memory, scalar fast paths,
  signed sums and stored-array experiments that do not establish a safe benefit.
  No experimental SQL, migration, generated column or cache change is retained.
- [x] Add one retry only for an acknowledged, non-invalidated PostgreSQL
  statement-timeout error. Re-admit a complete read after rollback through the
  same bounded executor, allowing queued controls first. Operator cancellation,
  other errors, discarded sessions and shutdown do not trigger this retry.
- [x] Demonstrate native Red4/Green20, then72 related checks including a
  disconnected waiter. Correct the real-PG fixture's public from/to aliases;
  retain those invalid-fixture logs separately. Valid Red2/Green2 proves a new
  consistent snapshot, unchanged2s/3s deadlines, exact late-fill fees/counts,
  stale timestamp/cooldown after two failures and no leaked connection.
- [x] Run broad native2,464 +11 subtests. SQL, response schema, TTL, worker/pool
  limits, engine and ledger remain unchanged. Persistent failures still degrade
  explicitly; a successful retry is a complete new observation, not relabeling.
- [x] Finish broad PostgreSQL354 checks and source commit. No shipping SQL,
  schema, TTL, statement/I/O deadline, worker or pool change.
- [x] Build/verify image `f4d90bc7` from `d2cdf2a36`: nine runtime hashes and
  all197 Python package versions match. Three packaged E2E groups pass. All
  four wait/CPU single/persistent-timeout cases pass: a single timeout recovers
  without stale replies, two retain bounded stale fallback; each reconciles
  120 fills/fees/positions with unchanged engine/owner/session and no HALT.
  Stopped Demo upgrade/restart preserves four ledger hashes and matches exposure.
- [x] Repeat the unchanged healthy standalone hour after shorter checks:
  79,200 HTTP200 responses, no errors/stale, primary/auxiliary p99
  615.65/565.42ms,3,594 exact synthetic fills/fees/positions and27.68MiB retained
  growth. Independently restore the pre-soak backup into a network-isolated
  clone, match four ledger hashes, and verify stopped/no HALT/pending state.
  Preserve the earlier failed short probe; this does not close full R1/R2.
- [x] Preserve the new 300s probe's p99 failure (1,094.27/1,112.11ms against
  1,000ms); all6,600 responses are HTTP200, no stale response,300 fills match.
  Worker-queue p99 is500.25ms, pool-admission p99 is0.070ms and API SQL p99
  is52.57ms. These separate cumulative distributions cannot be added or matched
  to individual requests; the untraced run does not establish an exclusive cause.
- [x] After the independent hour and Demo restoration, collect one isolated
  same-image300s traced probe with unchanged load/resource/latency settings.
  All34 >1s clients match API roots;12 wait >500ms for a worker. Balance and
  positions wait1,066/1,092ms although their largest SQL spans are12.03/4.82ms.
  Review API-only loop/GC overlap and trace losses; retained p99 is823.21/734.17ms
  with no stale/error and300 exact fills. Publish the bounded explanation and
  independently verify restored Demo/hash/configuration state. Original failure
  remains failed and its exclusive cause unproven; no new runtime tuning.

## Admin database worker lanes — requested 2026-11-04

[TDD plan](MM_ADMIN_WORKER_LANES_TDD_PLAN.md),
[scoped report](../../reports/performance/mm-admin-worker-lanes.md).

- [x] Demonstrate native Red for heavy work blocking balance, reserved admission
  and default-page progress. Keep one operations and one heavy job within the
  existing two-worker/connection limits; controls retain priority.
- [x] Finish native2,494 +11 subtests, PostgreSQL355 and Swagger checks;
  commit the verified source phase. Thirty new native cases and one PG case.
- [x] Verify image086454de, three packaged E2E groups, four controlled report faults and the
  unchanged short/hour load gates; retain failures and compare route latency.
- [x] Independently verify stopped Demo/configuration/ledger restoration and
  publish final evidence. Older image acceptance is not transferred.

The phase is scoped complete. Preserve the pre-window owner-state timeout/
supervisor recovery as an unresolved causal observation in the linked report;
the passing measured hour is not a clean-startup or universal availability claim.

## Quote-recovery current-image completion — 2026-11-03

- [x] Full unchanged R1 including one-hour soak/six faults, and matched shipping R2;
  independent image/source/17-fixture/resource verification.
- [x] Current-image read-only Demo preflight and private DB backup; restore absent
  stopped service with original configuration/history intact.
- [x] Nine profile/mode cases, actual600s Random step and active spot/both restart;
  independently confirm407 canceled post-only orders.
- [x] Separate bounded maker-only40USDT-per-market trial: one spot and four perpetual
  natural fills, original fees/quantity precision, active filled restart and dedup.
- [x] Final11 unique fill rows, unchanged original six; positions/cash match OKX;
  original200/400 catalogue and canonical container restored, trading stopped.
- [x] Publish scoped reports/evidence, retain first failed burst and probe limits,
  clean owned temporary resources and commit. No live trading or cloud deployment.

[Full local report](../../reports/release/mm-quote-recovery-release.md),
[connected Demo/fill report](../../reports/demo/okx-demo-quote-recovery.md).

## Earlier read-only preflight on39198499 — 2026-11-02

- [x] Validate both Demo credential sets, account mode, authenticated REST,
  public/private WS, instruments, balances, pending orders and held positions
  with SDK code inside accepted image `39198499`; send no mutations.
- [x] Read recovery from an isolated copy of the absent service's original
  volume, with original mounted read-only. Verify stopped/acknowledged/noHALT,
  no pending commands/mutations, unchanged historical ledger and exact perpetual
  base quantities. Verify original volume/private config hashes unchanged.
- [x] Publish scoped evidence, remove task-owned copies/containers and preserve
  the distinction from active-session or running-service recovery acceptance.

[Result/evidence](../../reports/investigations/mm-engine-probe-controlled.md).
No original service restoration, order/session/account write, migration, repair
or active MM session is included. Earlier connected acceptance below retains
its original image/date scope.

## Latent-fix candidate connected Demo follow-up — 2026-11-01

This is a new connected scope on image `3f5cbd0f` / manifest `6e54a348`.
The completed local R1/R2 gates remain in the
[release report](../../reports/release/mm-release-closeout.md#latest-completed-rerun--scoped-result).
Earlier Demo results below retain their original image scope.

- [x] Read-only preflight: both credential sets, account mode, public/private
  REST/WS, balances, no pending orders, no HALT/unfinished commands, and exact
  contract-to-base perpetual position reconciliation.
- [x] Back up the persistent DB privately; preserve the old stopped container;
  upgrade the canonical service while trading is stopped. Verify unchanged
  order/fill/executor/position hashes and original private YAML/environment.
- [x] Run the nine fixed profile/market cases with existing policy budgets,
  post-only quotes, independent exchange identity/precision checks and Stop.
- [x] Verify the configured Random step and active spot restart with pinned
  session/anchor/deadlines; verify active both-market restart with durable holds.
- [x] Independently reconcile terminal orders, new fills/original fees, spot
  balance changes and perpetual positions; repeat fill readback for deduplication.
  Zero new fills must remain a stated limitation, not positive-fill acceptance.
- [x] Align the deployment revision label with the candidate image; verify a
  stopped startup with unchanged ledger hashes and original trading config.
- [x] Leave the service running with trading stopped, no pending orders or
  unresolved mutations; publish scoped evidence, limits and rollback instructions.

No private catalogue budget or safety guard is changed; no forced fill, live
account, database repair, HALT reset, or Cloud Run deployment is authorized by
this verification. Raw local results: `build/okx-demo-latent-20261101/`.

Result: [connected report](../../reports/demo/okx-demo-latent-fixes.md), [sanitized evidence](../../reports/demo/okx-demo-latent-fixes-evidence.json). New natural fills observed: 0; earlier positive-fill evidence is not relabelled.

## Submission-block log clarity follow-up — source only

- [x] Red/Green expected session/readiness log severity and preserve ERROR for
  unsafe/unknown guards, missing wiring, HALT and pending mutations.
- [x] Preserve connector failure events, executor failure state/list/retry counts
  and ordinary transport diagnostics; add the new tests to the release target.
- [x] Verify targeted native/OKX/tracker/logging regressions and isolated
  PostgreSQL ownership/submission/shutdown behavior; document severity semantics.

[Verification report](../../reports/platform/submission-block-logging.md): native
239 plus4 subtests and PostgreSQL50 pass. At this checkpoint the source change
was not packaged into Demo image `3f5cbd0f`; the following image-release scope
packages it separately without relabelling the earlier connected acceptance.

## Submission-log image release follow-up — 2026-11-01

Candidate `a190b024` packages source `4af96c0ee`, as a four-Python-module
local derivative of `3f5cbd0f`; native dependencies are unchanged. This is not
claimed as a clean Dockerfile rebuild. Earlier image results retain their scopes.

- [x] Verify the four packaged module hashes and real imported paths. Run a
  network-disabled packaged smoke through connector, failure event, executor and
  structured formatter: expected severities/reasons, redaction and unchanged
  failed-state/retry semantics.
- [x] Run the targeted spot-outflow, funding and LIFE-MM simulator E2E groups,
  including packaged runtime/ABI smoke, on this exact candidate.
- [x] Run one unchanged full R1: warm300/mixed900/burst90/soak3600 and six recovery
  cases, with frozen source/image, original latency/zero-error/memory budgets and
  independent scope/ledger/continuity checks. Preserve any failed attempt.
- [x] Run and independently verify matched ordinary-entrypoint R2 warm300/mixed900
  using the passing full R1 baseline; do not claim a second full-hour soak.
- [x] Fresh read-only Demo preflight, private DB backup and stopped upgrade only
  after local gates pass. Preserve existing config, history, positions and rollback;
  align revision with the image and verify stopped startup/health/recovery.
- [x] Publish exact results and remaining limits, update checklists and commit.

Result: [release/stopped-upgrade report](../../reports/release/mm-submission-log-release.md)
and [portable evidence](../../reports/release/mm-submission-log-release-evidence.json).
R1 soak p99 is643ms with zero unexpected API errors and14.39MiB retention growth;
all six faults pass. R2 warm/mixed p99 is849/347ms with zero errors. No new active
Demo session or natural fill is claimed for this logging-only image.

Raw evidence: `build/submission-log-release-20261101/`. No forced fill, trading
Start, HALT reset, DB repair, live account or Cloud Run deployment is included.
The earlier unresolved burst p99 is not closed by a later passing run.

## Prior release verification — completed scopes (`5973410d`)

Updated 2026-11-01 (local time). Immutable candidate image `5973410d` / manifest
`50a95f3a` is unchanged. Measurement source is frozen at `b7ba104e7`.
Both R1 and R2 below are complete for this scoped local release verification.
The earlier verified defects remain closed; historical attribution limits remain explicit.

- [x] **R1 — Complete current-image release verification:** run
  `20261101-011658-1465e8` passes warm300/mixed900/burst90/continuous-soak3600 and
  all six recovery cases as one full gate. Independent checks confirm the same
  image/source hashes,58 continuous minute samples, all three Random profiles,
  unchanged healthy process identities and4,884 matching unique fills/positions.
  Soak p95/p99:147/717ms, zero unexpected errors. Retention16.08MiB and raw
  cgroup growth58.13MiB both meet64MiB. Native114 and bounded matched probes
  precede this gate. Temporary containers are removed; stopped Demo fingerprints
  remain unchanged. See the [closeout report](../../reports/release/mm-release-closeout.md).
- [x] **R2 — Complete the passing-baseline shipping control:** run
  `production-control-20261101-024951-b873b2` passes ordinary-entrypoint
  warm300/mixed900 against the actual passing R1, with matching image/effective
  YAML/client/exchange hashes and resources/history/pool5/TTL15. Warmup p95/p99
  52/289ms; mixed55/396ms. Zero unexpected API errors,1,200 matching fills/fees/
  positions and unchanged process identities. Twelve probe episodes recover,
  longest1.182s; no unplanned child start/replacement. Temporary containers and
  the task-owned idle-sleep assertion are removed; Demo fingerprints remain
  unchanged. Independent source/scope checks pass; scoped release evidence is
  published. No Cloud Run deployment or connected trading occurs in closeout.

R1 uses2CPUs/2GiB, history100,000, original rates and error/latency budgets, plus
128 test-only captures with512KiB ring/32MiB stream caps per role. R1 acceptance
is not a claim of zero transient stalls:98 healthy-sequence probe episodes
recover, longest4.591s, with one declared diagnostic signal intervention. No
unplanned child replacement/HALT occurs. Original historical causes remain
unresolved below; no speculative runtime fix or attribution is claimed.

The retention contract declared before this run is
`memory.current - (file - shmem)`: exclude disk cache, retain tmpfs/shared memory.
Keep64MiB growth, each process's RSS/thread/FD limits and the1.5GiB absolute
container peak including all cache. Raw growth is also published. Partial or
inconsistent breakdowns fail; old runs keep their original conservative rule
and failed result. Both retention and raw growth pass in this new run.

Previous full failures `20261031-222312-6f060d` and
`20261031-231819-7fe2f2` are preserved. Confirmed GC callback deadlock and on-loop
quantile-export cost are test-tool defects, verified with Red/Green diagnostics.
Bounded100,000-fill query replay does not reproduce the old cancellation, so no
SQL/cache/pool rewrite is made. See the
[measurement follow-up](../../reports/investigations/mm-measurement-remediation.md).

## Historical causes — unresolved evidence

The [2026-11-01 focused review](../../reports/investigations/mm-historical-cause-review.md)
completes the bounded investigation and fixes a newly reproduced test-sampler
timestamp defect (119 tests plus a packaged old/new comparison). Existing runs
keep their original hashes and outcomes. Historical attribution remains limited;
the new sampler does not retroactively establish missing causes.

| ID | Observation still unexplained | Current disposition |
| --- | --- | --- |
| H1 | Simulator delivered 498.033/s against the 500/s minimum in one earlier combined run | Exact old client already switches burst mode before baseline counters. Failed inner-burst count/duration are missing; cause remains unproven. Later passes do not explain it. |
| H2 | Older sustained supervisor-liveness incident | Original logs lack typed probe history/final blocked stack. Timing alone does not establish SQL timeout or the separately fixed recorder deadlock as its cause. |
| H3 | Five recovered timeout probes over 7.206s and sampled scheduled lag6.723s in the failed current-image burst | Engine processes fills inside the episode; no completed engine full GC overlaps. Episode length does not prove a continuous total freeze. Other causes remain unproven; no speculative runtime fix. |

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
so H3 was unresolved and R1/R2 were pending at that checkpoint; current status above supersedes that execution status. No speculative runtime
fix or threshold waiver is made. Capture limits and new short observer/DB-wait
observations are explicit in the report.

See [incident investigation](../../reports/investigations/mm-incident-investigation.md).

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
  [report](../../reports/demo/okx-demo-current-image.md) and
  [sanitized evidence](../../reports/demo/okx-demo-current-image-evidence.json).

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
retry, canonical API is up with trading stopped and no HALT. See [follow-up report](../../reports/demo/okx-demo-natural-fill.md)
and [scoped evidence](../../reports/demo/okx-demo-natural-fill-evidence.json).

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
  all three profiles and no retention violation. That failed p99 measurement
  remains failed; current candidate acceptance is completed above.
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
  [investigation](../../reports/investigations/mm-engine-liveness-investigation.md).

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

Progress: [packaged release report](../../reports/release/probe-release-20261030.md).

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

Evidence: [Random handover investigation](../../reports/investigations/mm-random-handover.md).

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
[follow-up report](../../reports/investigations/mm-burst-followup.md). The later current-candidate attempt fails burst latency as recorded below;
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
  from runtime controller/OKX bugs and the latency observation unresolved at
  that checkpoint.

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
- **Historical unaccepted checkpoint — superseded by current R1/R2:**
  No ordinary-entrypoint acceptance control is run from this failed
  baseline. Continue only with evidence targeting the specific latency episode;
  do not repeat hour gates blindly or relax limits to force closure.
- [x] Verify unchanged Demo image/control/four ledger fingerprints and clean
  temporary test containers. Publish the outcomes and remaining limits in the
  [follow-up report](../../reports/investigations/mm-burst-followup.md).

## 2026-11-03 follow-up: expired submission at Random rollover

- [x] Reconcile the retained Demo HALT and pending spot intent with venue evidence and operator-reviewed maintenance before new trading. See [incident](../../reports/demo/okx-expired-submission-investigation.md).
- [x] Requalify the expiry-corrected image `7dca3ce9`; repeat nine Demo cases,
  Random, perpetual/both and restart. Verify seven natural fills at REST order
  aggregate level. Passing offline load on e9845e3f does not qualify later source.
- [x] Verify each new natural trade ID and individual fee through REST fills;
  a later read matches all seven new fills. Preserve the original failed
  observation and passing aggregate/account checks separately.

- [x] Review the independently discovered stop-checkpoint latch and apply the
  [separately approved proposal](../../reports/demo/okx-checkpoint-stop-recovery-proposal.md).
- [x] Fix shutdown retry checkpointing with TDD; package `51d4d879` and verify
  stopped upgrade/restart without changing ledger hashes. Initial targeted
  qualification was subsequently extended by the final load item below.
- [x] Run and independently verify new full R1/R2 on final `51d4d879`; all
  windows/faults pass and the idle Demo is restored. Active nine-case Demo
  remains scoped to `7dca3ce9`; see [final release](../../reports/release/mm-checkpoint-final-release.md).
