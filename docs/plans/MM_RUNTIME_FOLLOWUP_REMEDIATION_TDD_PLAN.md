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
