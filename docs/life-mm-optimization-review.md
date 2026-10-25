# LIFE MM cache, SQL and task lifecycle optimization

Reviewed on 2026-10-25, after the original P5 acceptance. This follow-up reuses
the existing aiohttp handlers, cachetools cache, SQLAlchemy models, PostgreSQL
transactions and V2 lifecycle. It adds no Redis service, cache abstraction,
database table or migration. No real OKX account or Cloud Run service is used.

## Changes and measured results

| Area | Before | After |
|---|---|---|
| Daily cache hit during another market's cache miss | Waited behind the aggregate lock despite a valid cached response | Returns before acquiring the lock; a regression holds the unrelated query open and requires the hit to complete |
| Uncached daily report over 100,000 fills, native local PG, p50 / p95 | 812.38 / 1,250.46 ms | 503.93 / 512.86 ms |
| Unchanged balance snapshot for 100 currencies, SQL statements | 100 | 1 |
| Same snapshot transaction, p50 / p95 | 184.38 / 311.65 ms | 28.23 / 30.39 ms |
| Controller stop while startup awaits public data | Stop flag left the startup task waiting; startup failure could skip cleanup | Cancel the retained control task and run cleanup in `finally`; wait for cleanup before retiring the strategy |
| Immediate stop/start cycles | Old control work could overlap a replacement | 20 immediate restarts: 21 starts/cleanups, peak one active control operation, zero remaining active operations |

The report median improves approximately **38%** and the 100-currency snapshot
median approximately **85%**. These are local warm-buffer observations, not
Cloud SQL latency guarantees. Both measurements use 11 timed samples after a
warmup, with Python allocation measurement in a separate invocation. Native
macOS arm64 Python uses disposable PostgreSQL 14 over loopback. The final timing
subset ran without concurrent Docker builds, load tests or other regression jobs.
The before/after jobs create equivalent fixtures in fresh disposable databases.

Raw local measurements: `build/reporting-optimization-before.json` and
`build/reporting-optimization-idle-final.json`; the latter includes paired
`EXPLAIN (ANALYZE, BUFFERS)` plans against the same database.

## 1. Daily cache contention

`AdminAPI.daily()` now checks the existing 32-entry TTLCache before taking the
aggregate lock, and checks it again after admission on a miss. Hits for one
market/date range therefore do not wait for a slow miss for another range.
Misses remain serialized to bound database pressure; this deliberately avoids
introducing an unbounded per-key task/lock registry. Identical concurrent polls
still share the first completed result. TTL, filter/timezone isolation, original
observation timestamps, disabled-cache mode and uncached errors are preserved.

Tests: `test/hummingbot/cli/test_admin_daily_cache.py`. The new blocked-miss test
fails before the change and passes afterward. No ownership, HALT, order admission,
pending intent, price/risk input or exchange truth is read from this cache.

## 2. Daily SQL: remove measured compilation overhead

The two existing recorder aggregates are retained. Attempts to combine them
using `GROUPING SETS` or a shared materialized CTE passed correctness checks but
were slower: measured medians were approximately 1,144 ms and 1,379 ms. The CTE
trial also spilled temporary data and its correlated fee lookup repeated work.
Neither implementation is shipped.

The accepted change sets `jit=off` **only inside the daily read transaction**.
For this bounded UI workload the PostgreSQL fee expansion's estimated cost
triggers expensive JIT compilation. Paired query plans show fee-query execution
at approximately 603 ms with the default setting and 168 ms with JIT disabled.
The fill-total query remained around 360 ms. Query plans and timings are retained
in the raw report; they do not force index use or change database-wide settings.

The full daily request now uses **six statements rather than five**: one local
setting, database time, fill totals, fee totals, funding and balance snapshots.
This is a measured latency improvement, not a reduction in SQL statement count.
There is no per-fill/per-day SQL loop. REPEATABLE READ still gives all aggregates
one database snapshot. A concurrent-fill regression verifies consistent totals
and fees; tests also cover multiple fee lines/currencies, empty fees, percentage
fees, rebates, exact decimals, market separation, late events and day boundaries.
The test verifies that the connection's JIT setting is restored after the
transaction. Larger histories need new measurements before further tuning.

## 3. Batch balance observations

`save_daily_balances()` now submits the complete observation through one typed
`VALUES` / `INSERT ... SELECT ... ON CONFLICT` statement. SQL still checks
freshness against the database clock and retains the 60-second update cadence,
monotonically newer observations and the caller's successful owner fence.
SQL filters stale/future observations before selecting the newest duplicate
account/environment/currency/timezone/day key. Missing/unverified balances are
excluded; nullable cash/equity fields and decimal-string inputs are supported.
No rows means no SQL. All stored instants remain UTC.

This removes the per-currency round trips while keeping the work proportional
to payload size in one transaction. The 100-currency measurement is a scaling
fixture; the LIFE/USDT account usually has far fewer currencies. Peak traced
Python allocations for this transaction increased from about 0.73 to 1.16 MiB
because it builds one batch; this is not a steady process RSS measurement.

Tests: `test/integration/postgres/test_admin_daily.py` and
`test_admin_reporting_optimization.py`. Query budgets failed before batching;
correctness, ownership fencing, freshness and rate limiting pass afterward.

## 4. Await controller and public-data teardown

Controllers retain their control-loop task. Stop cancels a blocked startup or
control operation; immediate restart awaits the previous generation before
acquiring subscriptions again. Runnable cleanup runs in `finally`, including
startup failure/cancellation, and supports asynchronous cleanup. LIFE reference
book release is awaited rather than detached into a fire-and-forget task.

Strategy shutdown stops controllers and cancels/drains the action listener
before awaiting controller retirement, so queued creates cannot be consumed
while slow cleanup runs. It then closes executors and awaits public connector
shutdown. The data provider retains its network/rate shutdown tasks until they
finish. Existing synchronous `stop()` entry points remain available; shutdown
uses the added `wait_closed()` boundaries. Existing service shutdown deadlines
still apply.

All still-retiring generations remain tracked if a replacement is stopped
before its first tick. Further starts wait for that entire set, and cancellation
of a replacement does not cancel its predecessor's cleanup. Completed tasks are
removed directly after draining rather than waiting for deferred callbacks;
this also avoids spinning on already-completed futures. Scheduling the controller
coroutine directly avoids an unawaited inner coroutine on cancel-before-start.

Regressions cover blocked startup, failed startup, asynchronous reference release,
20 immediate restarts, repeated replacement cancellation before the first tick,
delayed public connector stop and the queued-action race
during delayed cleanup. These failed before the corresponding fixes. They are
included in `make test-cloudrun-regression`.

Asyncio creation traces in the Docker rehearsal also located the remaining
pending-task diagnostic in `PerpetualDerivativePyBase`: network stop reset the
funding notifier but never cancelled its funding-payment poller. Restart could
then overwrite the only task reference while the old poller waited on the old
event. Network stop now cancels and drains both funding tasks before resetting
their notifier/state. A real OKX-adapter regression reproduces three polling /
stop cycles without contacting OKX; it fails before the fix. All **137 OKX
perpetual adapter tests** pass afterward. The funding calculation, recorder and
existing notification/reset policy are unchanged.

## Cache and N+1 decision

- Existing history query budgets remain orders ≤3, fills ≤1 and performance ≤1
  SQL statements, independent of page size. Existing V2 PnL and connector account
  observations continue to serve reporting; FE polling does not add exchange
  account requests per HTTP request.
- The removed scaling problem was per-currency snapshot SQL, not a history
  lazy-loading N+1. Reconciliation still deliberately reads current exchange
  orders/fills within its existing bounds; a TTL cache would weaken that check.
- Keep the existing process-local statistics TTL (`ADMIN_STATISTICS_CACHE_TTL_SECONDS`,
  default 5, 0 disables). Redis and `CACHE_BACKEND` settings remain unnecessary
  for the measured workload. Revisit only with a concrete shared read consumer.

## Verification

The PostgreSQL suite passes **111 tests**. The final Makefile regression groups
pass **149 + 11 + 902 + 187 = 1,249 tests**, including cache/lifecycle coverage.
Swagger drift passes. Existing Pydantic/SQLAlchemy deprecation and test-mock
warnings are retained in the logs.

The final standard-Dockerfile artifact is
`sha256:159c8ad60be29c1feef1a49abb734c6cfb59e827a2fa6ca1f000bb7caa9a03af`
(1,364,478,594 bytes), registry manifest
`sha256:1e4e70d5d07376dd0c2a4b49a4a5bc0587389725cb66cc5a7f91bb3e50156c09`.
All eight changed runtime sources inside it match the checked-out implementation
at `742290063`; hashes are retained in `build/mm-optimization-source-hashes.json`.

Final affected-image E2E:
`build/e2e-offline/20261025-120422-fed1ed/result.json`, with asyncio debug enabled.
All nine market/profile combinations pass, along with weighted/stale references,
restart anchors, random steps, expiry, matched reconciliation and DB-loss HALT.
Captured final-image logs contain **zero pending-task destruction diagnostics**
and **zero unawaited-coroutine diagnostics**. This selects `life-mm`, so its
`release_scope_complete` is intentionally false: it does not recertify every
release scenario. The earlier six-scenario/upgrade/rollback rehearsal passed on
the pre-funding-fix image `11b65ea94b75`; it is separate evidence.

The final artifact also passes the standard standalone **90-second burst**
profile (`diagnostic=false`, unchanged deployed refresh/cooldown settings):
`build/mm-load/20261025-121207-de205b/result.json`. The bot has 1 CPU / 2 GiB;
PG has 2 CPUs / 512 MiB, with 100,000 seeded orders and fills, both markets and
the existing independent exchange ledger. This run does not overlap another
build or test workload.

| Burst measurement | Result |
|---|---:|
| Offered / completed reads | 1,800 / 1,800 |
| Actual completion-span rate | 19.98 reads/s |
| Aggregate dispatch-inclusive read p95 / p99 | 63.58 / 518.13 ms |
| Daily-route p50 / p95 / p99 | 4.80 / 569.73 / 728.46 ms |
| Peak process RSS | 313.7 MiB |
| Book backlog drain after the 5x burst | 0.08 seconds |
| Actual switch / stop / start | 3.69 / 0.53 / 2.07 seconds |
| Unique exchange / recorder fills | 90 / 90 |
| Unexpected HTTP errors / budget violations | 0 / 0 |

The market burst delivers approximately 499.82 updates/s. Position/fee/unit
comparisons and reconciliation match; three daily snapshot rows exist. Ten
same-key retries retain one command identity and the original deadline, and
ten expected stale-version conflicts are counted separately. History query
counts remain ≤3 / ≤1 / ≤1 for orders / fills / performance. Final burst logs
also contain no pending-task destruction or unawaited-coroutine diagnostic.

The daily-route p95 still exceeds the aggregate 500 ms threshold. The accepted
budget applies to aggregate reads, not each endpoint; the 90-second per-route
sample is not directly comparable to the original 60-minute soak's distribution.
A stricter chart-refresh SLO or much larger history needs a separate measured
change. No new cache service is justified by this run.

Implementation commits:

- `dd68d41a8`: cache-hit fast path.
- `5c56e7869`: snapshot batch and reporting JIT setting, with real-PG measurements.
- `e4afbcac6`: awaited controller/reference/public-network retirement.
- `c00285ab7`: retain all still-retiring generations through cancel-before-start.
- `742290063`: cancel/drain the orphaned perpetual funding poller on network stop.

Reproduce the isolated SQL measurements:

```sh
HBOT_PERFORMANCE_REPORT=build/reporting-review.json \
  python scripts/test_postgres.py \
  -k 'reporting_large_history_measurement or reporting_snapshot_measurement' -s
make test-cloudrun-regression
make test-postgres
```

The original 60-minute P5 soak belongs to the immutable image and source hashes
in [life-mm-load-acceptance.json](life-mm-load-acceptance.json). It must not be
relabelled as a soak of this follow-up image. OKX Demo P6 still requires the
dedicated account credentials/instruments and isolated DB configuration.
