# Reporting and startup optimization

Date: 2026-11-02 (Asia/Saigon). Ordered work follows
[the implementation plan](../../docs/plans/MM_REPORTING_STARTUP_OPTIMIZATION_TDD_PLAN.md).
Final image/load verification is pending until all source phases finish.

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
The setup's bulk insert/update places its512MiB cgroup near capacity; verification repeats the experiment through the actual retained runtime code.
Earlier workload-dependent SQL regressions remain in the
[SQL investigation](../investigations/mm-reporting-latency.md).

Deterministic Red tests fail twice because aggregates inherit two parallel
workers. Green checks require zero workers, JIT off and unchanged2s deadline
before both aggregates, plus restoration and pool cleanup after success/failure.
Both Reds and all17 selected PostgreSQL cases pass after the change (265
cases deselected), including existing snapshot/fee arithmetic and100k checks.
The actual source-code million-row repeat passes separately. Broad final gates
are pending. [Portable measurements](mm-reporting-startup-evidence.json) retain
failed controls, successful mitigation and attribution limits.

## Final image and load

Pending. Freeze the completed application/measurement source, then run one full
R1 and matched ordinary-entrypoint R2. Preserve failures and historical
attribution limits; no threshold or safety gate is relaxed.
