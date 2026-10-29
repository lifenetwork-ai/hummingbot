# Daily reporting latency remediation

R1 decision (2026-10-29): keep the existing SQL. None of the tested alternatives
establishes a safe improvement across the declared workloads. The confirmed
reporting timeout remains a scalability limitation; no SQL fix or new release
acceptance is claimed. R2/R3 work is tracked in the
[plan](../docs/plans/MM_REPORTING_LATENCY_TDD_PLAN.md).

The [p99 investigation](mm-p99-investigation.md) confirmed a daily original-fee
aggregate exceeding PostgreSQL's 2s statement deadline during a 100k-fill load.
The process continued. Separately, the exact original p99 trigger remains
unattributed. These are related observations, not proof of one common cause.

## R1 comparison

[Evidence](mm-reporting-latency-evidence.json) preserves individual samples and
EXPLAIN summaries; [SQL alternatives](mm-reporting-fee-candidates.sql) retain the
queries. Raw complete plans/helpers/logs remain in ignored
`build/reporting-latency-fix/`. These are SQL diagnostics, not image/load gates.

The existing `seed_history` fixture creates 100k/1M closed orders and fills.
Every eleventh fee array is empty; the others have two entries, USDT/ETH and a
negative USDT rebate. Repeated amounts have seven values (absolute order-ID hash
modulo seven, plus one, divided by 10^12). Diverse amounts use the order-ID suffix
divided by 10^24, with the second amount negated and divided by seven. Both cases
include multiple original currencies and exact NUMERIC arithmetic. All queries
select both markets, UTC, no pair restriction and the same half-open range.
Compared result rows were exactly Decimal-equal. Production range, DST,
conversion, late-fill and filter semantics are unchanged because no SQL change
is retained.

Exploratory PostgreSQL 14-alpine had no per-container CPU/memory limit. Median
wall milliseconds over three randomized-order samples:

| Fills / payloads | Baseline | Pre-group payload | Sign-filtered sums |
|---|---:|---:|---:|
| 100000 / repeated | 336.45 | 114.15 | 334.60 |
| 100000 / unique | 199.81 | 1064.72 | 203.23 |
| 1000000 / repeated | 3623.80 | 2128.56 | 3119.25 |
| 1000000 / unique | 3512.68 | 10575.64 | 2997.74 |

Pre-grouping helps repeated fees but substantially regresses diverse fees.
Text-payload lateral rescans also regressed the initial 100k experiment. A small
sign-filtered advantage in some cases does not establish repeatable benefit.

A second paired experiment limited PostgreSQL to **2 CPU / 512 MiB**, using five
randomized-order samples per case. It computes paid/rebate using two filtered
SUM states and derives net in PostgreSQL NUMERIC, avoiding a third SUM state:

| Fills / payloads | Baseline ms | Derived-net ms | Change |
|---|---:|---:|---:|
| 100000 / repeated | 283.16 | 250.45 | -11.55% |
| 100000 / unique | 288.74 | 286.81 | -0.67% |
| 1000000 / repeated | 4260.36 | 4661.01 | +9.40% |
| 1000000 / unique | 4469.63 | 5272.57 | +17.96% |

The candidate regressed both million-row cases, so its uncommitted runtime edit
was reverted. Each comparison passed its equality checks; neither is a passing
latency gate. Test-only setup/EXPLAIN connections deliberately allow 120s;
production statement (2s), I/O (3s), pool and HTTP deadlines remain unchanged.
The initial experiment timed out at pytest's 120s per-test limit before the
million-row result; reruns used an explicit 900s diagnostic test limit. Both
completed runs cleaned their disposable databases. The real Demo remains stopped.

Caching or a stale fallback cannot make a full-history aggregate cheap. It can
bound polling pressure and preserve explicitly qualified operator information.
If long-history queries still exceed their budget after R2/R3, narrower requested
ranges, resource sizing or a separately designed incremental aggregate require
fresh evidence; this phase adds no ledger, migration, index, Redis or service.

## R2: qualified last-good daily fallback

Implemented after the architecture experiment, retaining the existing production
entrypoint. A successful daily observation may be reused on an identifiable
transient read failure, bounded to **less than 60s from its original database
observation**, across both wall and monotonic clocks. The existing in-memory
cache retains at most 32 canonical timezone/date/market/pair keys. Fresh TTL
remains configured by `ADMIN_STATISTICS_CACHE_TTL_SECONDS` (default five); zero
disables both reuse and fallback. No Redis, new migration or reporting ledger.

Responses explicitly label `freshness: "stale"`, observation age, an English
message and a 1–5s retry hint. A five-second retry cooldown coalesces failed polls
without changing source time or extending snapshot life. Relative reporting-day
rollover, clock reversal/invalidity, future observations and different filters
prevent reuse. Successful refresh replaces the observation. Cancellation and
permanent schema/authentication/validation errors never trigger fallback.
Trading, ownership, controls, risk admission, current balances and positions do
not read this cache. A daily balance series still means last observed that day,
not current available funds. See the updated [FE contract](../docs/fe-integration.md).

Red: two new API behavior tests failed, ten pre-existing failure/isolation cases
passed. Focused Green expanded to 53 tests. Broad CLI/admin settings: **700 pass**.
Real PostgreSQL daily/reporting/funding selection: **22 pass**, including an
actual 2s statement timeout, bounded fallback, unchanged timeout setting,
recovery and connection return; a real undefined-table error does not fall back.
Schema generation/check pass. One native fixture initially lacked the new
load-health sibling import; that test bootstrap was corrected before the broad
green rerun. No production failure is attributed to that fixture.

The load generator now counts HTTP 200 with explicit stale daily statistics as
an unexpected error. This preserves the zero-error healthy gate; qualified
operator data does not make an expensive or unavailable query healthy. Final
image/E2E/load verification and R3 worker-queue timing remain open. The SQL cost
and uniquely attributing the historical p99 spike are not declared fixed.

## R3a: separate API admission from connection-pool wait

The mounted test wrapper now measures `api_worker_queue_ms` from submission to
physical worker start. It includes waiting in the priority dispatcher and
thread startup; it excludes the subsequent DB call. This differs from existing
`pool_queue_ms` / `pool_checkout_ms`, measured only after a worker begins DB work.
An optional `api_database_worker_start` trace event carries scalar wait, pending
count, control/report role and request identity. Trace span duration is the
brief observation event; the preceding wait is its `queue_wait_ms` attribute.

The wrapper preserves copied request context and the original executor's
capacity/priority/cancellation rules. Cancelled queued calls are not counted as
physical starts; running calls remain admitted until physically complete.
Samples remain bounded (one extra fixed family at the existing 12k ceiling).
No call arguments/results or new production metrics endpoint are recorded.

Red: three missing-hook failures. Green: **18 selected timing/trace/executor
tests pass**. The production scheduler, worker counts, DB pool and timeouts are
unchanged. Measurement of a reproduced live burst is still required before
attributing queue pressure or proposing any further runtime fix.
