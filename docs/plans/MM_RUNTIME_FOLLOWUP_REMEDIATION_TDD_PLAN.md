# Runtime follow-up remediation — TDD

Date: 2026-11-07 (Asia/Saigon). Baseline `b9b73366d`, runtime recorder `c9889da28`.
Follow the [review](../../reports/README.md#historical-records) (Git: `reports/investigations/mm-runtime-followup-code-review.md`).
Reuse existing service incidents, risk guards, supervisor and native reports.

## R1 — Availability boundaries (F1/F2)

- [x] Red: stopped malformed checkpoints and live risk-status collection/validation
  faults must produce explicit unavailable diagnostics without an observer exception.
- [x] Preserve raw checkpoints; block running intent on invalid durable risk data
  through existing recovery HALT. Never reset guards or invent policy/binding values.
- [x] Preserve cancellation, ownership loss, SQL/recorder/checkpoint failure and
  cleanup/restart fences; do not catch critical failures as optional reporting.
- [x] Green: healthy recovery clears observational incidents; real PostgreSQL
  verifies stopped control availability, retained checkpoint/HALT and blocked sends.
- [x] Record scoped evidence, update checklist and commit this phase.

R1 source checks: initial 12 Red failures, seven additional checkpoint/Stop Red
failures and two corrected operator-view Reds. Thirty native and five PG cases
are added. Related native87 / PG10 pass before final frozen-source broad checks.
Invalid checkpoints retain their raw data; running intent uses existing HALT.
View failures return null with scoped incidents, while critical exceptions and
real cleanup/checkpoint writes still propagate. Operator guidance is English.

## R2 — Steady readiness (F3)

- [x] Red/Green: remove only the duplicate steady no-bootstrap readiness traversal;
  preserve strict final readiness, preparation/anchor writes and bootstrap checks.
- [x] Keep per-order/after-throttle checks fresh; no cross-call cache. Measure
  reference/exposure call counts and verify recovery/failure paths; commit the phase.

## R3 — Remaining report costs (F4/F5/F6)

- [x] Measure actual valuation-ready/report/serialization costs before selecting
  further changes. Retain small/large fixture and freshness/error tradeoffs.
- [x] TDD any justified narrow optimization; keep new/held exposure, finite-value
  rejection, fees, scope filters, strict serialization and mutation isolation.
- [x] Document intentionally deferred changes if measurements do not justify them.

## Verification

- [x] Run required final native and PostgreSQL regressions on frozen source;
  publish findings, retained failures and source/image acceptance limits; commit.

No real Google/GCP/OKX operation, Demo data repair, production deployment or
inherited new-image/hour acceptance. Rebuild/qualify changed runtime separately
before deployment. Keep health thresholds, durability and DB pool limits unchanged.

Frozen source `504343b9b`: native **2,780 + 11 subtests**, full disposable
PostgreSQL **523**, Swagger and formatting/lint pass. New coverage totals 57 native
and 5 PG, included in those totals. Phase commits: availability `45707b06e`, steady
readiness `1f4f19de6`, fresh exposure/held summary reuse `504343b9b`.
See [report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md) and
[evidence](../../reports/implementation/mm-runtime-followup-evidence.json).
JSON-copy consolidation is deliberately deferred after measurement. No changed
image/E2E/hour gate or unique historical-probe attribution is claimed.


## Image/load follow-up — requested 2026-11-07

The source-only results above retain their original scope. New image verification
is recorded separately; keep the previous gate's workload, budgets and failures.

- [x] Build/pin an amd64 image from the verified source; record packaged source
  hashes, runtime smoke and targeted image E2E evidence.
- [x] Run unchanged full baseline with operations/security and bounded incident
  capture: warmup300, mixed900, burst90, soak3600 and all five fault windows.
- [x] Exercise enabled drawdown/report paths in a separate bounded diagnostic;
  do not use it to replace the unchanged baseline or claim continuous MM activity.
- [x] Publish image-specific results/limitations and close only verified items.
- [x] After tests finish, remove exact unused bot images; retain current candidate,
  accepted rollback, all container-referenced images, other projects and volumes.

Image `bf4e1f77…` passes the new full gate (soak p99 306.91 ms, zero unexpected
healthy errors, 21.89 MiB retained growth, 4,885 exact simulated fills), four targeted
E2E groups, enabled-enhancement90 and all five faults. Three recovered healthy
probes remain qualified. One unused image removed; container/volume inventories
retained. See [image report](../../reports/release/mm-runtime-followup-load.md) and
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).


## Probe follow-up — requested 2026-11-07

The three recovered episodes on `bf4e1f77…` retain their accepted gate scope.
Read raw samples as well as selected spans; asynchronous span CPU is inclusive
of interleaved loop work. Keep native owner/session and durable-send fences fresh.

- [x] Review warmup/mixed/soak code and retained traces without changing probe,
  I/O, statement, retry, CPU, pool or load budgets.
- [x] Red: demonstrate three fill flushes, including one Order-only flush caused
  by the history lookup, with real PostgreSQL and ownership. Two failures/five
  safety checks pass on the original code.
- [x] Green: coalesce the pending Order with status/fill at the next existing
  checkpoint flush. Preserve atomicity, duplicate/late events, stale-owner and
  commit failures, ack-after-commit and cleanup behavior.
- [x] Measure matched statement/flush/ownership counts and timing; qualify
  savings without attributing all historic probe episodes to this change.
- [x] Measure funding reader construction/read paths; change only independently
  confirmed unnecessary work, with Red/Green and unchanged freshness/fencing.
- [x] Run final source regressions; publish scoped results and commit.

Runtime `e030cfcdc`: native **2,780 + 11 subtests** pass. Full disposable PG run:
**530 pass / 1 fail** (old steady-fill assertion expected 11 statements, observed
10). Correct only that exact-count assertion; rerun it with all eight new cases:
**9 pass / 522 deselected**. The full suite is not rerun after this test-only
correction; retain both logs in the [evidence](../../reports/performance/mm-fill-flush-evidence.json).

No current-image/hour or new real-Demo acceptance is inherited by changed source.
No general recorder worker, trading-state cache or new probe timeout is introduced.

## Fill-flush image/load follow-up — requested 2026-11-07

- [x] Build/pin the changed amd64 image and verify packaged source identities.
- [x] Run the four targeted image E2E groups and enabled-enhancement diagnostic.

The first initial-position restart attempt times out waiting for PID1 shutdown
(9.5s). Retain that result; an unchanged-image/unchanged-budget retry passes,
shutdown1.489/1.630s. This does not establish the first failure's exclusive cause.

- [x] Repeat the unchanged full baseline: warmup300, mixed900, burst90,
  Random soak3600, security/operations and all five fault windows.
- [x] Publish this image's measured results, failures and probe/continuity limits.
- [x] After successful gates, remove superseded unused bot images; retain the new
  candidate, one accepted rollback, all container references and volumes.

Keep existing Demo unchanged. Preparation and raw runner output live in
`build/fill-flush-release-20261107/`; do not transfer the prior image's pass.

Image `837e7997…` (build `9ca14a148`, recorder `e030cfcdc`) passes the new full
gate/five faults, four targeted E2E groups after the disclosed restart retry,
and enhancement90. Soak p99 **360.68 ms**, zero unexpected healthy errors,
**24.05 MiB** retained growth and **4,884 exact simulated fills**. Eight probes
recover (mixed1, soak7); this does not establish uninterrupted quoting or their
unique cause. Image `f74d2a2a…` removed; retain rollback `bf4e1f77…`, all ten
container references and fourteen volumes. [Report](../../reports/release/mm-runtime-followup-load.md),
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).

## Residual probe investigation — requested 2026-11-07

- [x] Inspect all seven current soak episodes and actual request-time stacks,
  including captures omitted by the selected-span summary. Preserve uncertainty
  about the inter-run count increase; no threshold/budget changes.
- [x] TDD a narrowly scoped fresh ownership read without a separate read COMMIT;
  retain the physical advisory-lock session, fresh authority checks, rollback and
  original isolation for every write. Measure before keeping the change.
- [x] Run targeted ownership/recorder/recovery checks and source regressions;
  publish failed attempts, measurements and remaining image/probe limitations.

Use disposable local PostgreSQL only; no existing-Demo data or credentials change.

Targeted PG **80 pass**, including sixteen new cases and all five I/O modes. Seven paired real-PG rounds
retain every fresh SELECT and remove the separate read COMMIT. Component p50
2.262→1.219 ms; all samples retained. A controlled 450ms read-COMMIT delay
makes the unchanged 300ms production probe time out with the legacy path; scoped
read mode passes. This does not uniquely attribute the seven historical episodes.

Reject the first isolation-execution-options prototype: 200 reads accumulate
400 pool-reset callbacks. Scope the documented driver flag instead; the unchanged
SQLAlchemy isolation metadata and pool reset callbacks remain bounded. Retain the
prototype Red/Green and intentionally interrupted regression as rejected evidence.

Keep legacy behavior for existing SQLAlchemy/DBAPI read transactions; mode is
changed only for an idle read scope. Preserve the original three-second loss-of-
response deadline; the owner connection is now permanently closed. Discard a
physical session if restoring transaction mode fails, rather than pooling it.

Frozen source `d9488143d`: native **2,780 + 11 subtests**, full PG **547 pass**
(772.09s). Frozen paired batch means/read 2.209→1.210ms; injected read-COMMIT
delay still times out the unchanged probe on the legacy path (302.3ms), scoped
read returns 200 at 2.2ms. At this source-only checkpoint, no new image or hour gate was claimed;
the subsequent image gate is recorded below. [Investigation](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07),
[evidence](../../reports/performance/mm-owner-read-evidence.json).

## Ownership-read image load — requested 2026-11-07

- [x] Build/pin the new amd64 image; verify packaged source/config hashes.
- [x] Run targeted availability, drawdown, recovery and initial-position restart
  E2E, followed by the enabled-enhancement90 diagnostic.
- [x] Repeat the unchanged full baseline, security/operations, Random soak3600
  and all five fault windows; retain probe, latency, memory and exact ledger results.
- [x] Publish scoped comparison and failures; close only verified items and commit.
- [x] After successful gates, remove superseded unused bot images; retain the new
  candidate, preceding accepted rollback, all container references and volumes.

Raw preparation/run artifacts: `build/owner-read-release-20261107/`. Existing Demo
remains outside the test network and is not queried or modified.

Image `e00cef38…` (build `ff6b85ded`, runtime `d9488143d`) passes all gates on
first attempts: four targeted E2E groups, enhancement90 and unchanged full
baseline/soak3600/five faults. Soak p99 **410.20 ms**, zero unexpected healthy
errors, **28.59 MiB / 64 MiB** retained growth and **4,884 exact simulated fills**.
Measured probes: mixed API1, soak engine5, plus one engine probe between warmup
and mixed; all recover. Compared with preceding `837e7997…`, soak probes fall
7→5 but soak p99 rises 360.68→410.20ms and retention 24.05→28.59MiB. No uniform
speedup, continuous-quote or unique-cause claim. Remove unused `bf4e1f77…`;
retain new candidate, accepted rollback `837e7997…`, all ten containers and
fourteen volumes. [Current report](../../reports/release/mm-runtime-followup-load.md),
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
The earlier fill-flush image report and disclosed retry remain at Git `ff6b85ded`
under those paths; newer gates do not erase earlier attempts.

## Fresh write-fence identity projection — requested 2026-11-07

- [x] Red: prove a write fence currently retrieves unused recovery/session JSON;
  preserve a fresh owner/session/advisory-lock check on every call, including
  later flushes in an already-fenced transaction.
- [x] Green: project only owner-session proof for write fencing; preserve full
  state for observer/admission, original transaction modes, deadlines and losses.
- [x] Measure paired full/projection reads on disposable PG with explicit payload
  sizes; keep timing separate from full-image latency and residual attribution.
- [x] Run ownership/recorder/recovery regressions, document failures/limits and
  commit. The accepted `e00cef38…` image remains unchanged until a new image gate.

Red1/4 pass; initial draft2 failures/73 pass retained (old spy and SQL-prefix
injection mismatch). Corrected targeted87, native 2,780 + 11 subtests and full
PG 556 pass. Every fresh SELECT remains; write-proof result columns14→1.
Seven alternating pairs, 25 reads per batch: median batch means/read
1.187→0.969ms (two empty JSON payload fields), 2.871→0.967ms (two64KiB fields).
Zero pool callback growth; serialized result bytes are not network counts.
[Report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#fresh-write-fence-identity-projection--2026-11-07),
[evidence](../../reports/performance/mm-owner-read-evidence.json). This source
change does not inherit `e00cef38…`'s image/load acceptance or uniquely resolve
the remaining probes. No new image or existing-Demo operation was performed.

## Reporting snapshot copy cost — requested 2026-11-07

- [x] Red: count redundant traversal of retained reporting history; test that
  updates/failures never mutate previous snapshots or live connector data.
- [x] Green: copy only mutated reporting containers, retaining the original
  strict JSON normalization/detachment boundary and unavailable-state behavior.
- [x] Measure paired baseline/current capture CPU, wall time and allocation on
  explicitly sized fixtures; do not attribute old probe/GC episodes to this alone.
- [x] Run relevant reporting/publication/availability tests and native regression,
  update evidence/checklists and commit. No new image/load/Demo acceptance implied.

Eight new cases; frozen original-function Red1/7 pass. Native2,788 + 11 subtests
and related PG 36/520 deselected pass. History traversals2→1; paired component
wall costs0.08774→0.05168ms (small) and2.96243→1.26469ms (larger fixture).
Allocation peak increases6,984 bytes in the small fixture, decreases95,304 in
the larger. No whole-engine latency, leak or exclusive probe-cause claim.
[Report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#reporting-snapshot-copy-cost--2026-11-07),
[evidence](../../reports/implementation/mm-runtime-followup-evidence.json).
Accepted image `e00cef38…`, existing Demo, containers and volumes stay unchanged.

## Combined write-fence / reporting image gate — requested 2026-11-07

- [x] Build/pin the amd64 image containing `5c421b121` and `745a86e3c`; verify all
  packaged source/config hashes and retain the preceding accepted evidence.
- [x] Run the four targeted image E2E groups and enabled-enhancement90 diagnostic.
- [x] Run a separate activity300 diagnostic with original refresh timing, capturing
  MM cycles, submit/cancel timing and independent simulator quote observations;
  distinguish missing evidence, guard waits, replacement and actual cycle delays.
- [x] Repeat the unchanged baseline/security/operator mix, Random soak3600 and five
  fault windows; retain failures, probe/latency/resource and exact ledger results.
- [x] Publish comparison and quote-observation acceptance guidance without silently
  agreeing a numeric production quote-coverage SLO or claiming continuous quotes.
- [x] After success, remove only superseded unused bot images, keep the new image,
  preceding accepted rollback, all container references/volumes; update and commit.

Raw preparation/run artifacts: `build/runtime-copy-release-20261107/`. Existing
Demo and real Google/GCP remain outside these local image tests. The unchanged
baseline does not enable MM activity instrumentation; the extra diagnostic cannot
replace its sixty-minute acceptance gate or retroactively prove quote continuity.

Result: image `a6fe88c0…`, build `745a86e3c`, passes all stages on first attempts.
97,800 main / 24,450 auxiliary healthy HTTP200; soak p99 406.14 ms, 24.26 MiB retained
growth and 4,885 exact simulated fills/fees/positions. Eleven soak engine and three
mixed engine probes recover without healthy child replacement. Their frequency
and burst p99 503.57 ms regress versus rollback `e00cef38…`; attribution remains
open. Activity300 shows completed cycles and two-sided venue point samples during
its two probes, with finite/non-two-sided refresh observations elsewhere.
No continuous-quote or exclusive-cause claim. The 120-second warning is not a
production replacement SLO. Only unused `837e7997…` removed; all 10 containers /
14 volumes retained. [Report](../../reports/release/mm-runtime-followup-load.md),
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).


## Combined-image regression audit — requested 2026-11-07

- [x] Compare the exact packaged before/after source and hot-path call structure;
  inspect ownership freshness, reporting detachment and unchanged budgets.
- [x] Reconcile probe intervals with retained stacks/spans and distinguish async
  inclusive duration, rolling metric distributions and per-window API latency.
- [x] Run an ABBA short comparison of immutable `e00cef38…` / `a6fe88c0…` images
  with the same isolated fixture, additional bounded stage timings and frozen
  rates/resources. Keep every attempt and both improvements/regressions.
- [x] Review loop-blocking diagnostic code and reproduce any confirmed defect
  before changing runtime or instrumentation. Do not identify one old incident's
  cause from a stack overlap alone.
- [x] Publish the measured decision to keep/revert/fix, scoped verification and
  unresolved attribution; preserve current release evidence and commit.

The short comparison is diagnostic, not a replacement release/soak gate. Current
source and immutable images stay fixed during comparison. No existing Demo or
cloud account is used. Artifacts: `build/runtime-regression-audit-20261107/`.

ABBA120 all pass: A p99 449.07/755.18 ms, B 427.69/495.84 ms; both B runs have
one recovered engine probe, A none. Retain the signal without declaring an
exclusive cause or uniform speedup. Six Red failures reproduce test incident
output holding the producer lock; bounded history snapshot/output separation,
GC lock-reentry coverage and out-of-order analysis yield 82 targeted passes.
The first updated-fixture B diagnostic passes with p99 258.09 ms, zero measured
probe transitions and 120 exact simulated fills; it does not close hour-gate
attribution. All 1,224 packaged runtime hashes stay unchanged. Keep the production
changes/both images; no fresh production optimization or threshold adjustment.
[Audit and decision](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#combined-image-regression-audit--2026-11-07),
[portable evidence](../../reports/implementation/mm-runtime-followup-evidence.json).

## Full load after incident-lock fix — requested 2026-11-07

- [x] Pin existing image `a6fe88c0…`, verify all 1,224 packaged hashes and record
  the test-fixture change from the preceding full gate. Preserve its evidence.
- [x] Execute the original full baseline attempt with operations/security and
  bounded incident capture. Preserve its fail-fast mixed failure and unrun
  burst/soak/fault phases, without changing rates/resources/thresholds.
- [x] Review all retained probe markers, healthy/fault log phases, exact ledger,
  query-count limits, retention and process identities; disclose capture losses.
- [x] Publish the comparison and fixture/image scope, verify existing containers
  and volumes are preserved, update current checklists and commit.

Raw runner/evidence: `build/incident-lock-load-20261107/`. The mounted fixture uses
the incident-lock correction from `dfad4a4d1`; no new image, extra stage timers,
MM activity/full trace, actual OKX/Google/GCP or deployment action is included.

First attempt exits 1 on mixed `read_p99` and `auxiliary_read_p99`: **1,315.91 /
1,169.10 ms** against 1,000 ms. Warmup passes; all 24,000 main/6,000 auxiliary reads
return HTTP200 and 1,200 exact simulated fills/fees/positions reconcile. Warmup 1 /
mixed 6 engine probes recover, with zero healthy log errors/replacements. Eight
premeasurement ERROR records are four distinct local `SubmissionBlocked` IDs,
absent from simulator ledger. The runner stops before burst/soak/faults. Keep
this historical attempt failed; do not reinterpret the prior 120-second pass as
an hour pass. Its report/evidence remain in Git `41d290393` at the current release
paths. Full coverage with the later quantile fixture is qualified separately below;
the earlier accepted full gate remains in Git `dfad4a4d1` at those paths.

## Slow-cluster follow-up: quantile export work — requested 2026-11-07

- [x] Inspect the failed minute's retained server/DB/CPU evidence; distinguish
  request latency, quota throttling and blocking stacks from causal attribution.
- [x] Red: count avoidable complete-sort work for four quantiles; preserve exact
  nearest ranks, empty/large/small rings, integer precision and snapshot isolation.
- [x] Green: reuse installed NumPy to select the four ranks for large rings;
  retain bounded short-ring sorting and the existing worker/queue/cadence limits.
- [x] Measure frozen before/after functions on native and the exact amd64 image;
  retain all alternating pairs, small-ring cost and heartbeat outliers.
- [x] Repeat the unchanged full workload with `--keep-going` so any latency
  failure remains failed while burst/soak/fault evidence is still collected.
- [x] Publish full results and scope; close acceptance only if every original
  budget passes. Do not claim the component explains the entire historical cluster.

The component tests prove excess measurement work, not a unique production bug.
All production code, image, rate/resource limits, quantile definitions, sample
counts and health/durability checks retain their original scope. Raw TDD/pairs:
`build/latency-cluster-fix-20261107/`.

First revised-fixture full attempt passes all four healthy windows, the complete
Random soak3600 and five faults. Main p99 warmup/mixed/burst/soak:
**312.31 / 366.46 / 168.04 / 287.63 ms**. All 97,800 main / 24,450 auxiliary HTTP200,
zero unexpected healthy errors/stale responses or budget violations; 4,884 exact
simulated fills/fees/positions reconcile. Soak retained growth 23.16 MiB/64 MiB;
58 running samples, max gap 65.51 s and PIDs 1/32/33. Healthy engine probes remain:
mixed 1 / soak 2, recovering in 1.053–1.177 s without healthy child replacement.

Source `41d290393`: 80 targeted tests before load, 81 after a supplementary
long-ring integer-precision case. All 1,224 packaged hashes stay unchanged;
all existing 10 containers/14 volumes/images retained. Host changed from battery
on the failed attempt to AC here, so exclusive inter-run causation stays open.
[Latest full verification](../../reports/release/mm-runtime-followup-load.md),
[portable evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
Raw full runner: `build/quantile-export-load-20261107/`.

## Bounded book collection and disabled-debug work — 2026-11-08

- [x] Audit synchronous recorder/report paths for avoidable traversal/formatting;
  preserve fresh ownership, risk validation and strict report detachment.
- [x] Red: verify market-data collection consumes only the existing configured
  depth + 1 per side, with unchanged persisted empty/short/deep book payloads.
- [x] Green: bound the collector iterator before materialization; preserve SQL,
  collection cadence, disabled default and cancellation behavior.
- [x] Red/Green: eliminate disabled DEBUG formatting in PositionHold without
  changing financial calculations, duplicate handling or enabled log messages.
- [x] Measure isolated component costs and run the affected regression suites;
  publish results and scope without borrowing the earlier image's load pass.

The prior full gate applies to shipping source through `745a86e3c` and mounted
quantile fixture `41d290393`. Any production change here remains source-verified
until separately qualified on a new image. No automatic Demo or image cleanup.

- [x] Red/Green: construct only the returned performance DTO for cache hit/miss;
  preserve cached PnL/counter detachment and fresh accumulation across calls.

Native result: **972 passes + 9 subtests** (104 targeted + 5 included). Seven
alternating paired component samples retain every empty/shallow/deep book and
both cache states; disabled-debug text/precision and PnL/dedup are covered.
[Measured source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md),
[evidence](../../reports/implementation/mm-runtime-followup-evidence.json).
No full-load result is inherited by this source delta.

## Quote calculation and anchor fallback — 2026-11-08

- [x] Red: count repeated allocation reads while comparing exact Decimal prices
  and quantities across side/level, budget changes, context precision and bootstrap.
- [x] Green: skip the superseded upstream formula only for enabled spread policy;
  reuse that invocation's allocations for bootstrap, preserving rounding order.
- [x] Red/Green: check book emptiness without complete snapshot/DataFrame creation;
  preserve fresh timestamp gates, explicit anchor requirements and invalid-book rejection.
- [x] Run controller/guard/executor regressions and paired native component
  measurements; retain disabled-policy costs and all attempts; commit each phase.

The existing image gate still predates this source delta. No persistent quote,
price, allocation or authority cache is added. Volatility window selection is
outside this change; retain existing NATR, candle gaps and freshness behavior.

Quote phase source checks: Red **8 fail / 8 pass**; Green **134 pass** across
spread/inventory/volatility/bootstrap suites. Sixteen new parameter cases include
three levels and two successive budgets each; quote values remain exact at
Decimal precision 9/28 with ROUND_UP and the existing bootstrap ROUND_DOWN.

Anchor phase: Red **16 fail / 6 pass**; combined Green **1,010 pass + 9 subtests**.
Seven alternating native pairs preserve exact outputs. Six-level spread sizing
CPU 49.30→22.84 µs; bootstrap + spread 75.04→33.32 µs. Empty bid / 400 ask fallback
460.53→2.60 µs; ordinary valid-price/disabled-policy paths retain observed overhead.
[Measured report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md),
[evidence](../../reports/implementation/mm-runtime-followup-evidence.json).
No new image or full load acceptance is implied by this source phase.

## New-image verification on battery power — requested 2026-11-08

- [x] Record clean source `8544488ef`, initial Docker inventory and host power.
  Battery initially 82%; Low Power Mode remains enabled. Prior bot/rollback
  images are absent from this Docker daemon; unrelated images/volume are retained.
- [x] Build the amd64 Cloud Run target from current source, check size/runtime
  imports and record immutable image/config hashes.
- [x] Execute available offline E2E scenarios on that manifest, retaining the failed
  all attempt and thirteen passing remaining targets; current replacement all
  also passes. Coverage includes spread, bootstrap, volatility sizing and
  lifecycle. The original attempt omits upgrade; the later distinct-image check
  is recorded below. The candidate is never its own comparison image.
- [x] Run the unchanged full load profile with operations/security and bounded
  incident diagnostics, including sixty-minute Random soak and five faults.
- [x] Review all measured windows, ledger/fees/positions, process identities,
  retention/probes and power/sleep observations; retain failed attempts.
- [x] Publish image/fixture scope, remaining limits and acceptance; commit results.

Artifacts: `build/quote-runtime-release-20261108/`. Battery/Low Power Mode are
observed, not changed. Idle sleep is inhibited for the runner; lid closure,
forced sleep or exhausted battery can still interrupt the gate. No live/Demo
exchange, real Google/GCP, deployment or existing data maintenance is authorized
by this offline verification phase.

First execution: image `96368970…`, build `8544488ef`, 1,374,595,562 bytes,
1,225 source/config hashes verified. Warmup/mixed/soak pass; all five faults pass.
Burst remains **failed**: main p99 1,084.01 ms / auxiliary 1,151.07 ms against
1,000 ms. Zero unexpected healthy read errors; soak retention 26.09 MiB/64 MiB,
4,884 exact simulated fills and matching positions. Host changed from battery
82→53% to AC at 2026-11-08T17:16:58Z during soak; do not claim battery-only scope
or unique power causation. Original failed execution and all artifacts retained.

First E2E stops after six completed groups at `initial-position-restart`:
its live-mode fixture omitted the dummy perpetual credentials, while demo
constructor already provides the dedicated demo set. Runtime rejects Start with
configuration_unavailable, with zero accepted venue orders. Existing fixture
introduced in `1ee10e01bc`; this predates the new production optimizations.

- [x] Red: exercise actual seed scenario up to first boot with the real credential
  adapter; live fails missing all three perpetual variables, demo passes.
- [x] Green: supply the same dummy perpetual credentials as other both-market
  fixtures; 14 related native tests pass, including both live/demo cases.
- [x] Execute the corrected-host E2E rerun on the same immutable runtime; retain
  its failed inventory shutdown and thirteen passing targets. Preserve the first
  missing-credential failure and original attempt scope separately.

Only host runner `scripts/test_e2e_offline.py` and its test are corrected. The
image remains build `8544488ef`; `.dockerignore` excludes the host E2E runner
from the image. All 1,225 packaged hashes match the host-fixture checkpoint
`683773d34`, before the later runtime signal fix.
Report its corrected external fixture hash separately; no engine/controller/recorder, credentials gate or
runtime setting changes. The full-load runner retained its pre-fix loaded source
for the entire measurement, with all 1,254 captured source hashes unchanged.

## Shutdown signal follow-up — 2026-11-09

The corrected `all` E2E attempt passes fourteen groups, then fails inventory skew
at the unchanged nonzero-exit shutdown assertion. The trading core logs shutdown
complete, but Docker State was not saved before cleanup. A bounded Docker event
query returns no retained events; it cannot recover the original exit number or
prove a unique cause. Preserve this failed attempt.

- [x] Add bounded shutdown State evidence to the host fixture before cleanup,
  including timeout paths. Red 4 failures; related Green 18 passes; `683773d34`.
  Preserve 9.5 s, exit-zero/OOM checks, and exclude Config.Env/health bodies.
- [x] Reproduce a real signal while holding the supervisor Event condition:
  existing handler re-enters its non-reentrant lock and the hard deadline exits1.
- [x] Red/Green fix (`1916e048e`): signal handler records the request flag;
  supervisor checks it at existing 50 ms waits and before/after child launch.
  Ordinary cleanup still sets the Event; async self-pipe wakeup stays unchanged.
  No new thread, lock, deadline or restart policy. Red 1 fail; related Green75.
- [x] Finish the thirteen remaining/targeted old-image E2E groups (all pass). A passing retry
  does not retrospectively pass the failed `all` attempt or prove its cause.
- [x] Build/pin a new canonical image with the signal fix, verify packaged source,
  and rerun native/available E2E plus the unchanged full load/soak/fault gate.
- [x] Publish both failed predecessors and the new verification separately;
  retain original p99/probe attribution limits and original unrun upgrade scope.
  A separate technical upgrade passes later, without endorsing the failed predecessor.

The event-lock bug is confirmed by deterministic real-signal Red/Green. It is a
possible mechanism for the observed shutdown, not proof of that historical
failure. Image `96368970…` predates the fix. Its burst p99 failure is independent
and remains failed; the shutdown fix makes no latency or probe-cause claim.

Old-image execution closed: fourteen groups pass in the corrected `all` attempt
before inventory-skew shutdown failure; all thirteen individual remaining/targeted
groups pass afterward. Twenty-seven unique groups are covered across attempts;
upgrade is omitted at that checkpoint. This is composite coverage, not a passing
`all` execution. Its full load stays failed on burst p99. The source fix at that
checkpoint requires the separately qualified replacement image recorded below.

## Final signal-safe candidate — 2026-11-09

- [x] Build `d0791e563`, image `b090430c…`, 1,374,595,999 bytes; 1,225 packaged
  hashes match and all 1,254 measured source/fixture hashes remain unchanged.
- [x] Canonical native 2,861 + 16 subtests; paired old/new Linux-image real-signal
  contract confirms old exit1/new exit0. No threshold/deadline/resource relaxation.
- [x] All 27 available E2E groups pass in one all attempt; inventory shutdown
  exit0/non-OOM, 1.308-second exit observation/1.34-second complete helper metric.
- [x] All unchanged load windows and five faults pass: main p99
  569.69/273.48/297.81/313.00 ms; 97,800 main / 24,450 auxiliary healthy HTTP200,
  4,885 exact simulated fills/fees/positions. Retained growth 30.33 MiB/64 MiB.
- [x] Disclose three recovered healthy engine probes 1.116/1.083/1.057 s, no healthy
  replacement/errors, eight ERROR records limited to injected DB faults. No
  continuous-quote proof or exclusive power/p99/old-shutdown-cause claim.
- [x] Finish separate technical upgrade/takeover/rollback with distinct compatible
  test predecessor `96368970…`. This does not qualify that failed image as production rollback.

The failed `96368970…` burst/all attempts remain in Git `d0791e563` at current release
report/evidence paths; accepted a6 records remain in `8544488ef`. New AC results
do not retroactively pass old failures. Real Google/GCP, connected Demo and
production quote-coverage policy retain their external/historical scope.

Standalone technical upgrade subsequently passes with distinct compatible test
images `96368970…` → `b090430c…` → `96368970…`, preserving one deduplicated
fill/fee and the additive nullable fixture column. The all 27 raw upgrade flag remains false; combined local groups 28
are covered across two commands. This does not qualify `96368970…` as production rollback.
Unrelated containers/images/volume remain; real external acceptance is deferred.

## Managed Demo Random shutdown follow-up — 2026-11-09

- [x] Preserve the real-image failure: after active Random restart, a step change
  reaches the existing eight-second cleanup budget, engine exits/restarts and
  durable HALT remains. The helper's finally block issues Stop; actual account
  readback has no pending orders. Do not count this as a passing Random run.
- [x] Confirm the sequential `_refresh_orders` implementation dates to
  `dcaa46fd4d` (2026-10-24), before Google/KMS setup. Maker cancellation/retry
  accumulates retained order references requiring fresh terminal proof.
- [x] Native Red1 proves blocked sequential read progress; PostgreSQL Red1
  reproduces incomplete shutdown with twelve retained maker attempts. A separate
  PG Red1 identifies missing safe timeout diagnostics.
- [x] Reuse existing per-order status/fill APIs with at most three scoped async
  workers. Batch the shutdown call site; retain both fresh proof passes, exact
  proven-unsent exclusions, per-order fills-before-terminal ordering, the eight-
  second deadline, HALT, ownership and checkpoint guards. Cancel/drain siblings
  on error or cancellation. Emit safe `runtime_shutdown_timeout` metadata.
- [x] Related native22 / PG15 pass. Canonical native2,868 + 16 subtests pass.
  The preceding four dotenv-contamination test failures and two fixture-directory
  failures are retained separately; CLI tests now isolate developer settings
  without weakening runtime no-file assertions.
- [x] Full PostgreSQL regression557 passes; final targeted proof/log-format2
  also passes. Timeout reason, deadline and order count survive JSON formatting.
- [x] Commit `aba5d745a`, build/pin `4451974e…` (1,374,605,855 bytes); all 1,229
  packaged files match. Real-signal smoke and paired blocked-read witness pass.
  Targeted until-stopped Random/restart/Stop and managed lifecycle E2E pass on
  the pinned manifest. These are not a new all-case release or sixty-minute soak.
- [x] Obtain separate operator review; remove only this incident's HALT and add
  one audit with all other table/row fingerprints unchanged. Real Demo Random
  steps0/1/2 exercise all three profiles, same-session active restart and Stop
  pass. Disconnect closes long0.0100/short0.0032 BTC with seven partial fills;
  only selected envelope is removed. API reconfigure and stopped cold restart
  pass; all ten new fills match REST, old23 fills/3,513 order IDs remain.
  [Connected report](../../reports/demo/managed-local-connected-followup.md).
- [ ] Package the latest corrected source and run the next candidate's full
  release/soak before production closeout; earlier image results do not transfer.
- [x] TDD source fix for cleanup engine reporting: real-core Red1/control-pass1,
  related native96 and PostgreSQL109 pass. Cleanup has no executable strategy;
  use `strategy_name=None` and the existing configuration/recorder fallback.
  General discovery/error fallback, cleanup proofs and old attribution remain.
  The3.682-second historical warning is retained; this separate correction is
  not packaged or connected-Demo-retested yet. [Investigation](../../reports/demo/managed-local-connected-followup.md#subsequent-cleanup-reporting-investigation--2026-11-09).

Incident and pre-cutover backups/KEK are retained in the private
`~/.config/hummingbot/managed-demo-local-20261109` folder. The reviewed cleanup subsequently closed the held
long/short `0.0100/0.0032 BTC`. All23 old fills and3,513 old order IDs remain;
three earlier natural spot fills and seven cleanup fill parts match individual
REST quantity/price/original fee and order aggregates. Fee-adjusted BTC cash
remains exact. No recovery checkpoint or risk-stop flag has been edited.
