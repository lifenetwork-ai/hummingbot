# PostgreSQL performance and recovery review

Reviewed after port commit `ddf77c4a7`. This review preserves the existing engine,
recorder, ORM and in-memory PnL cache. It adds no cache service or dependency.

## Findings and changes

1. **V2 startup loaded unrelated history and large JSON snapshots.** It fetched
   every executor and position, constructed every `ExecutorInfo`, then selected
   controllers in Python. Startup now selects current controllers in SQL, streams
   executor performance fields in batches of 1,000 and excludes overridden
   controllers from position recovery. The existing PnL accumulator still performs
   Float-to-Decimal conversion; aggregation was not moved to SQL floating-point
   `SUM`. Invalid close types and non-finite PnL/volume fail recovery, and the
   streaming cursor closes even when the consumer raises.
2. **Pending-order reads scanned historical orders.** Both the submission guard
   and recorder tracking-state merge use `creation_pending`. SQL script `008`
   adds `(creation_pending, config_file_path, market)` on `orders`, plus
   `controller_id` on `executors`. They are ordinary additive, idempotent indexes
   registered in ORM metadata and checked by the existing schema validator.
3. **Recorder cache warmup selected the oldest 2,000 order mappings.** Limited
   reads now select the newest mappings and return them in ascending order.
   Recent fills are filtered by market before applying the limit, preventing a
   busy connector from displacing another connector's entire deduplication window.
   Unlimited order-history callers retain their original behavior.
4. **CLI history already avoids N+1.** Its joined Order load is retained. Tests
   require one SQL statement for both one fill and 1,000 fills, including access
   to order creation time after the SQLAlchemy session has closed.

## Measurements

The baseline ran before production edits; the final optimized measurements below
used the same fixtures and machine after Docker image builds/E2E had finished. Native macOS arm64,
Python 3.13.15, SQLAlchemy 2.1.1, psycopg2 2.9.13; disposable PostgreSQL 14 arm64 in
Docker, accessed over loopback. These are local warm-buffer measurements, not
Cloud Run/Cloud SQL latency, cold storage, peak RSS or throughput guarantees.

| Workload | Before p50 / p95 | After p50 / p95 |
|---|---:|---:|
| V2 startup: 20,000 executors, 2,000 selected across 20 controllers, 1 KiB custom JSON per row; 7 samples | 1,133.6 / 1,156.8 ms | 49.39 / 118.53 ms |
| Global pending lookup: 100,000 orders, one pending; 25 samples | 20.88 / 24.69 ms | 1.79 / 5.12 ms |
| Recorder pending lookup: same history, scoped by config/market; 25 samples | 21.02 / 24.66 ms | 2.06 / 2.82 ms |

V2 peak Python allocations measured in a **separate** `tracemalloc` run fell from
147.33 MiB to 3.57 MiB. Both versions use two SELECT statements, so this was excess
rows/columns and object construction, not an executor N+1 issue. PostgreSQL
`EXPLAIN (ANALYZE, BUFFERS)` changed pending reads from sequential scans removing
99,999 rows and touching 2,223 shared buffers to an index scan touching 3 buffers.
No planner settings force index use.

Artifacts: `build/postgres-performance/before.json` and `idle-final.json`.
The first optimized run in `after.json` measured V2 at 45.3 / 47.3 ms; the final
run includes explicit non-finite validation and exception-safe cursor cleanup.
Intermediate
`final.json` / `validated.json` results include the added tests and may reflect concurrent image
build load; do not compare those absolute timings with the idle baseline.
Timing samples are evidence; regression gates use data correctness, query count,
projection/filtering and query plans instead of environment-sensitive millisecond
thresholds.

The test suite also measures the complete fenced recorder fill transaction on
100,000 historical orders, with and without the pending index in the **same
disposable database**. Ownership checks and transaction fencing remain enabled.
After warmup it uses 11 SQL statements in either case; the resulting fill count
and exact scale-six amounts must agree. This distinguishes the cost of one lookup
from total write latency, which still includes database round trips and commits.
In the final 25-sample run, unindexed p50/p95 was **41.38 / 50.93 ms** and indexed
p50/p95 was **23.68 / 27.29 ms** (about 43% lower median).
It measures repeated fills after the first status transition; it does not quantify
the additional index-maintenance cost of order creation/ACK updates.

## Cache decision

No additional cache backend is justified by these measurements. V2 already caches
historical PnL in process and updates it after executor persistence; 2,000 repeated
performance-report reads across 20 controllers issue **zero SQL statements**.
Public rate-oracle sources already reuse `async_ttl_cache`/`cachetools.TTLCache`.
The measured bottlenecks were startup over-fetching and missing indexes.

There is currently **no `CACHE_BACKEND` / `REDIS_URL` runtime setting**. Adding
unused settings or a second copy of authoritative trading state would not improve
these paths. If a measured public/read-only workload later requires a shared cache,
use a small adapter around the existing cache boundary with
`CACHE_BACKEND=inmem|redis`, an optional secret-injected `REDIS_URL`, bounded TTL,
versioned keys and an explicit outage policy. Add hit/miss/expiry/concurrent-miss
and Redis-outage tests with that concrete consumer, not an unused abstraction.

Never serve ownership, control version, HALT, pending mutations, order/fill truth,
balances used to admit risk or transaction fencing from a TTL/shared cache.
Their freshness is a correctness requirement. The service's repeated ownership
reads and status publication were reviewed but retained; reducing them needs a
separate measured workload and race/ownership-loss proof.

## Verification and operational limits

- Query-count tests cover zero, one and 20 controllers; empty strategies issue no
  history query. Projection tests prevent accidental JSON hydration or per-row
  lazy loads. Early close and invalid-history tests check pool connection release.
- PnL, volume and close-type counts match the previous full `ExecutorInfo` path.
  Position selection, initial-position overrides and missing history are covered.
- A populated database accepts migration `008`, repeated replay and subsequent
  `ENABLE_AUTO_MIGRATION=false` startup without changing recorded orders.
- Bounded-history regression was reproduced against the previous implementation
  before the fix. A two-market, 2,005-orders-per-history fixture verifies the
  latest mappings and each market's fill window.
- New indexes add disk and write maintenance cost. Index creation uses the
  existing bounded migration runner and can wait on/block writers while building.
  For a large active database, stop trading and run `make migrate` in a maintenance
  window. A timeout fails startup; it does not permit trading with an unvalidated
  schema. With auto migration disabled, apply `008` before deploying this revision.
- Selected history still costs O(selected executor count) CPU. A million-row
  active controller, multi-hour fill-burst soak, index build duration on a large
  production database and cloud network latency have not been measured here.
  Consider retention/checkpoints only when that workload justifies changing the
  recovery model. CLI users can bound history with the existing date/row filters.

Reproduce the targeted measurements without reading local `.env` or contacting
an exchange:

```sh
HBOT_PERFORMANCE_REPORT=build/postgres-performance/review.json \
  python scripts/test_postgres.py -k 'performance or bounded_history' -s
make test-cloudrun-regression
make test-postgres
```

Verification after the final recovery guards: **926 selected regression tests**
and **71 real PostgreSQL integration tests** passed. The PostgreSQL suite includes
the 10 new performance/recovery cases; existing Pydantic/AsyncMock warnings remain.
All configured pre-commit checks passed on the changed files.
The final measurement subset also passed (11 cases selected, including one existing
performance case). All five rebuilt-image E2E scenarios and the matching offline
deployment preflight passed; the current manifest and evidence are linked below.

Use the Hummingbot Python environment. The disposable runner creates and removes
its own loopback-only database. Final container evidence is recorded in
[postgres-verification.md](postgres-verification.md).
