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

## R3b: traced 180s candidate diagnostic

Image `90490bab…` passes credential-free ABI/JIT/TLS/SDK/logging smoke. The complete
native regression passes **1,938 tests plus eleven subtests** and Swagger check;
full PostgreSQL regression passes **259 tests**. The image is 1,367,400,250 bytes
(8,075 bytes above the prior rebuilt image). It is a candidate, not deployed to
the persistent Demo or Cloud Run.

The 180s all-feature traced probe passes: p95 **200.99ms**, p99 **541.95ms**,
3,600/3,600 main reads, 900/900 auxiliary reads, zero unexpected errors and no
budget violations. It preserves the independent fill/fee/position checks. No
stale fallback was needed to turn a failure into success; the new load policy
would count any such response as an error. Raw artifacts and analysis are under
`build/mm-load/20261029-160614-f1bb2f/`.

Final rolling runtime distributions (from startup, capped per family, so not
identical to the HTTP window population):

| Measurement | p99 ms | Maximum ms |
|---|---:|---:|
| API submit to physical worker start | 171.74 | 480.51 |
| SQL connection-pool queue | 1.58 | 42.02 |
| Pool checkout including ping | 21.67 | 385.64 |
| SQL execution | 26.83 | 559.21 |
| Event-loop lag | 656.22 | 1070.93 |

This does not justify pool expansion. The slowest individually recorded request
took 1,210.98ms: 649.80ms before client dispatch, 457.73ms from dispatch to server
middleware entry, 102.12ms in the handler and about 1.32ms afterward. Its API
worker wait was only 2.54ms. Do not label all pre-handler delay as DB admission.
The largest sampled main-loop stalls include synchronous ownership/recorder DB
waits (`bounded_wait`) and validation/GC. Longest recorded gen2 GC was 363.67ms.
These sampled overlaps do not uniquely attribute the original historical spike
or prove one universal cause. No trading scheduler or fence was changed.

The diagnostic retained zero dropped child spans in selected slow roots, nine
loop-stall samples and 705 PostgreSQL samples; inspect the loss counters in the
portable evidence for coverage. Full final-image E2E, unchanged mixed/burst/hour/
fault gates and production-entrypoint control are still pending. Passing this
short probe does not close that release scope.

Full candidate image E2E now passes **16 scenario groups**, including compatible
upgrade/rollback from image `143ddfe2…`; immutable manifest
`sha256:11dfdc615cf6f865968f593e66e5496a02c9ce6948f15e65e82c8dfbcf6e97d5`.
Raw result: `build/e2e-offline/20261029-162008-7ac426/result.json`. The runtime
package manifest matches the predecessor exactly, and packaged API/cache/model/
Swagger source hashes match the reviewed files. The unchanged long load gates
and production-entrypoint control remain pending.

## Production-entrypoint control

The same immutable candidate and matched catalogue/client/exchange fixtures pass
300s warmup and 900s mixed through the ordinary production entrypoint, without
load-service timing wrappers, trace/sampler or GC callbacks. Warmup p95/p99
152.50/574.80ms; mixed **123.24/465.60ms**, zero unexpected errors in both.
Independent fill/position/fee checks pass. Raw evidence:
`build/reporting-latency-fix/production-control/result.json`. This single
controlled run is not a statistical speedup claim versus historical images.
It does not measure internal N+1, worker/pool waits or held-adapter counters,
and does not replace the running complete load/soak/fault gate.

## Follow-up: TTL eviction race caught before release

Final review found a defect introduced in the new R2 cache: `TTLCache.get()` may
succeed immediately before the TTL boundary, then a scheduler/GC pause crosses
expiry before direct deletion. `TTLCache.__delitem__` raises `KeyError` for that
expired entry, turning an ordinary cache miss into API 500. This is not an
upstream-engine defect or an explanation for the historical p99 spike.

Two deterministic Red tests pause between the real cachetools lookup and
eviction (`get` and `fallback`); both reproduce `KeyError`. Green replaces
direct deletion with `pop(key, None)`, preserving expiry/filter/clock rules.
All **55 focused tests pass**. No financial calculations, deadlines or capacity
changed. A fresh image and final verification are required for this source.

The first all-profile attempt on image `90490bab…` was intentionally interrupted
before soak to apply the fix. Its warmup passed p99 450.93ms with zero errors;
mixed was incomplete. Raw result and the explicit supersession annotation are
in `build/mm-load/20261029-171514-6a8ec8/`. Do not treat that interrupted run as
either a latency failure or completed release acceptance. Earlier native/image/
control results retain their exact source/image scope.

## Corrected candidate verification

The corrected image `58356746…` is built from commit `acad3f9ed…` and is
1,367,400,340 bytes. Full native regression passes **1,940 tests and eleven
subtests**, including both expiry regressions; Swagger check passes. Full
PostgreSQL regression passes **259 tests** in 469.47s. Credential-free runtime
smoke passes. Packaged API/cache/model/Swagger hashes match the source; the
runtime package manifest still matches both predecessors exactly.

All **sixteen E2E scenario groups pass** for this corrected artifact, including
upgrade/rollback from `90490bab…`. Raw result:
`build/e2e-offline/20261029-175226-2ef8dd/result.json`. Production-entrypoint
control and the unchanged all-profile load remain open. They are deferred while
the requested single-service supervision candidate is evaluated. Earlier image-specific results are not
substituted for those gates. The persistent local Demo remains stopped on its
existing image. No Cloud Run deployment or live trading is authorized by these
local tests.
