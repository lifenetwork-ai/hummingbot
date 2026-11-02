# Reporting and startup optimization

Date: 2026-11-02 (Asia/Saigon). Ordered work follows
[the implementation plan](../../docs/plans/MM_REPORTING_STARTUP_OPTIMIZATION_TDD_PLAN.md).
Source phases and final same-image local R1/R2 verification are complete.
[Portable evidence](mm-reporting-startup-evidence.json) preserves measurements,
failed controls and the remaining attribution limits.

## A — Daily request admission

The previous global lock made an uncached spot report wait for an unrelated
perpetual aggregate. Cancellation could release that lock before the physical
query ended, allowing a new poll to duplicate the same work. Three Red cases
reproduce independent-key blocking, canceled-leader duplication and missing
bounded key admission (three failures/four existing passes).

With caching enabled, canonical timezone/date/market/pair keys now share a
shielded in-flight refresh. Different keys use the existing database scheduler,
with its two workers, bounded queue and control priority. The registry has at
most12 outstanding keys, removed on success/failure/cancellation/shutdown. Full
capacity uses the existing503/unavailable behavior or a qualified last-good
fallback. The cache remains32 entries; TTL0 bypasses sharing and fallback.
No new worker, SQL connection ceiling, Redis, schema or trading cache is added.

Midnight regressions cover queued failures and relative/explicit requests that
initially shared one resolved range. A caller re-resolves after shared work and
performs a bounded fresh read if its range changed. Clock disagreement cannot
create an endless retry loop. Original observation times, fresh/stale labels,
60s fallback age, cancellation propagation and permanent-error handling remain.

Validation: **61 focused**, **889 CLI** and **23 PostgreSQL** tests pass
(256 PG cases deselected for this phase). Tests cover two uncached markets,
same-key cancellation,12-key admission,100 sequential keys with no registry
retention, shutdown retaining physical worker ownership, TTL0 and midnight.
Existing SQLAlchemy/Pydantic deprecation warnings remain. The initial test import
and one test filename were corrected before the retained Red/Green runs; their
launcher logs are preserved separately. These are source results, not image or
load acceptance.

Raw logs: `build/reporting-startup-20261102/a-*.log`.

## B — Historical startup

The history iterator now returns lightweight SQLAlchemy rows instead of mapped
`Executors` instances. It selects the same five columns in the same order,
emitting byte-identical SQL with the same1,000-row streaming batch. The existing
orchestrator still performs Float-to-Decimal conversion, ordered Decimal sums,
position-hold exclusions, close-type counts and finite-value validation.
No SQL SUM, query/index change, extra cache, checkpoint or schema is introduced.

A diagnostic-only paired experiment uses native Python and disposable PostgreSQL
14 limited to2 CPU/512MiB. Historical rows include1KiB JSON snapshots, while two
selected controllers exercise filtered and all-selected scopes. Each path is
warmed, timed three times, then measured separately with tracemalloc. Timings
are observations, not CI pass/fail thresholds or full bot cold-start guarantees.

| History / selected | ORM p50 | Row p50 | ORM Python peak | Row Python peak |
| --- | ---: | ---: | ---: | ---: |
| 20,000 / 2,000 | 35.70ms | 25.37ms | 3.35MiB | 0.71MiB |
| 100,000 / 100,000 | 1,551.12ms | 701.12ms | 3.43MiB | 0.71MiB |
| 1,000,000 / 1,000,000 | 17,842.12ms | 7,372.46ms | 3.43MiB | 0.72MiB |

All three comparisons preserve exactly equal cached performance and exactly
two startup queries; the selected-column SQL is byte-identical. Million-row
median executor-history initialization falls about59%, with about79% lower
Python allocation peak. The peak describes the measured scan, not total process
RSS. Measurements use a fixed baseline-then-candidate order with separate warmups;
database/host effects are not isolated into a universal production percentage.
The million-row diagnostic uses an explicit900s test timeout and180s seed budget;
production query/I/O/startup deadlines remain unchanged.

The deterministic cost regression first fails on2,000 unnecessary ORM load
events. The final iterator passes that regression and existing exact-value,
scope, invalid-history and cursor-cleanup checks. **84 native** and **12
PostgreSQL** cases pass (268 PG cases deselected). The measured candidate is the
same lightweight query retained by the source change. Raw comparison/helpers:
`build/reporting-startup-20261102/b-*` and `startup_benchmark.py`.

## C — Daily SQL scaling

The derived-net alternative did not provide a consistent win: five shuffled
100k-fee trials measured medians278.14/279.18ms (original/alternative) with
repeated fees, and286.14/268.69ms with unique fees. Original full daily reads
completed in500–526ms in those100k cases. No aggregate formula, Decimal handling,
original fee attribution, migration or index change is retained.

The million-row diagnostic exposed a shared-database resource failure, rather
than merely a slow UI response. On PostgreSQL14.15/arm64 limited to2 CPU/512MiB,
the extended-deadline candidate lost its backend to OOM. The original report
also lost a backend under its ordinary2s deadline. A fresh reader connection
after disposing the seeding pool still failed; retained seeding connection
memory alone does not explain the failure. Raw PostgreSQL logs and Docker OOM
state are preserved. DB process recovery must not be confused with no OOM.

A bounded row-count admission experiment also failed in that setup and was
withdrawn. It adds no API restriction, new environment variable or cache policy.
The retained mitigation instead disables parallel workers **only inside the
existing daily-report transaction**, alongside its existing local JIT setting.
This bounds database-process fan-out of the existing two API workers without
changing trading connections or global PostgreSQL settings. Commit and rollback
restore the original pooled-connection setting. Query formulas and the2s
statement deadline stay unchanged; a large report may still time out.

A controlled serial-read experiment on the same million-row fee fixture returns
SQLSTATE57014 for three individual reads and two concurrent reads, without a
backend loss or OOM. Seventy-one separate lightweight DB probes all succeed
(maximum35.35ms). This is bounded-query safety evidence, not a full trading-engine
availability guarantee or proof of the exact PostgreSQL allocation mechanism.
The setup's bulk insert/update places its512MiB cgroup near capacity. The
actual retained runtime passes an independent repeat: three single and two
concurrent reads cancel with57014 (2.02–2.53s wall time, first read2.17s), with
no backend loss/OOM;90 lightweight probes succeed, maximum66.59ms. The
statement timeout remains2s; wall time also includes cancellation/connection
work.
Earlier workload-dependent SQL regressions remain in the
[SQL investigation](../investigations/mm-reporting-latency.md).

Deterministic Red tests fail twice because aggregates inherit two parallel
workers. Green checks require zero workers, JIT off and unchanged2s deadline
before both aggregates, plus restoration and pool cleanup after success/failure.
The two previously failing tests and all17 selected PostgreSQL cases pass (265
cases deselected), including existing snapshot/fee arithmetic and100k checks.
The actual source-code million-row repeat passes separately. Full final
regressions and image gates also pass below. [Portable measurements](mm-reporting-startup-evidence.json) retain
failed controls, successful mitigation and attribution limits.

## Final image and load — completed

Candidate `39198499` packages source `9db4ff56a`, over clean-rebuilt diagnostic
base `eb72604c`. Exactly four changed Python modules match their source hashes
and real import paths; base layers and commit metadata match. Native dependencies
are unchanged. This is a local derivative, not another full Dockerfile rebuild.
Image size is1,369,049,423 bytes. Manifest digest starts `9f4a4497`.

Full native regression passes **2,265 tests plus11 subtests**, full PostgreSQL
**282**, and Swagger check. Spot-outflow, funding and LIFE-MM image E2E groups
all pass on the same digest, including the nine profile/market combinations.
Non-failing deprecation and mock-coroutine warnings remain in native logs.

The final load runs after source checks and image E2E, with application and
measurement source frozen across1,221 files. App limits remain2 CPU/2GiB,
PostgreSQL2 CPU/512MiB,100k historical fills, reporting TTL15s, and the existing
pool/worker limits. All original duration, error, latency, resource, retention
and recovery gates remain unchanged. Neither load attempt needed a retry.

| Run / window | Seconds | API p95 | API p99 | Unexpected API errors |
| --- | ---: | ---: | ---: | ---: |
| R1 warmup | 300 | 59.29ms | 317.85ms | 0 |
| R1 mixed | 900 | 74.54ms | 396.87ms | 0 |
| R1 burst | 90 | 257.77ms | 576.87ms | 0 |
| R1 soak | 3,600 | 76.84ms | 553.13ms | 0 |
| R2 shipping warmup | 300 | 50.52ms | 62.16ms | 0 |
| R2 shipping mixed | 900 | 54.29ms | 80.78ms | 0 |

R1 completes all six deliberate faults: feed loss, volatility defense, owner
restart, queued command expiry, database delay and database failure. The healthy
soak has58 samples with maximum65.59s spacing; supervisor/engine/API PIDs stay
unchanged and sampled runtime never HALTs. All4,886 exchange/recorder fills,
original fees,2 identified funding bills and positions reconcile. Retention grows
**14.03MiB** against the existing64MiB budget; peak cgroup memory is**758.98MiB**.
Engine/API per-role RSS median growth is7.54/6.36MiB; threads/fds stay within the
original bounds. Retention uses cgroup current minus reclaimable file cache plus
shared memory; raw cgroup median growth is44.66MiB, not relabelled as a leak.
Post-soak PostgreSQL cgroup counters show zero OOMs/OOM kills before DB fault
injection. These are scoped observations, not unlimited-duration guarantees.

R2 uses the ordinary shipping entrypoint and the passing full R1 baseline,
with matched image, fixture hashes, resources and TTL. Both300/900s windows
pass;1,200 cumulative fills and positions reconcile, and process PIDs stay
unchanged. Independent R1 and R2 verification passes. R2 is a20-minute shipping
control, not a second full-hour soak or Cloud Run deployment.

### Remaining attribution and coverage limits

- Instrumented R1 records54 recovered engine-probe episodes during the healthy
  window range,39 in soak; the longest reported recovery interval is2.623s.
  There is no child restart/HALT or unexpected API error, but stable PIDs do not
  prove uninterrupted quoting. R2 has zero recorded probe episodes in its shorter
  shipping scope. Different duration/instrumentation prevents attributing this
  difference uniquely to tracing or claiming the production path can never stall.
- Ten longest selected soak incidents overlap observe, recorder/fill and loop
  stall work. This is temporal correlation, not proof of one causal SQL/runtime
  defect. API trace capacity drops565 ordinary records; reserved GC/probe records
  have no reported drops, and incident caps do not suppress captures. Bounded
  records and selective span retention still prevent complete attribution.
- Random draws select `sideways` and `follow_token` during this hour. All three
  profiles are covered by separate same-image E2E cases, not all by this soak.
- The million-fill report remains subject to its2s statement deadline; successful
  cancellation does not produce a complete financial aggregate. Exact PostgreSQL
  OOM allocation and earlier burst/probe causes remain open. No whole-service
  upstream speedup or production profitability is inferred from these tests.
- No new connected OKX Demo session, Demo upgrade, private config/history change,
  real exchange trade or Cloud Run deployment occurs here. The existing Demo
  volume remains; the former container/image had been removed by Docker cleanup.

Raw final results: `build/mm-load/20261102-052812-326f41/` and
`build/service-supervision/production-control-20261102-065742-feb436/`.
Source/final pipeline, verifier logs, controlled failures and diagnostic helpers:
`build/reporting-startup-20261102/`. Native/SQL experiment details remain above.

Commits: A `21ce776c1`, B `c7cad9896`, C `9db4ff56a`. Documentation closeout
records the final accepted local scope without changing the tested application.
