# Runtime flow optimization

Status: runtime optimization complete; native/PostgreSQL regression, image E2E,
healthy load/soak and a separate five-fault retest pass. The first combined run
failed during fault preparation; that result is retained below. Reviewed 2026-10-27
(Asia/Ho_Chi_Minh), baseline
`6ef8465d8`. The changes reuse the existing engine, recorder, SQLAlchemy pool,
PostgreSQL queries and rolling outflow projection. No new service, cache backend,
schema migration or exchange mutation is introduced.

## Measured changes

| Flow | Before | After | Scope |
| --- | --- | --- | --- |
| Retained reader pool | `pool_size=3`, overflow 2 | `pool_size=5`, overflow 0 | Same configured five-connection ceiling; retain warm readers |
| Daily on 100000 fills, p50/p95 | 498.29 / 549.71 ms | 375.14 / 440.94 ms | Native loopback PG14, eleven warmed samples in fresh equivalent fixtures |
| Snapshot normalization, 100 currencies | 1.079 ms CPU | 0.955 ms CPU | Paired native snapshot/JSON replay, no DB/network |
| Snapshot normalization, four currencies | 0.088 ms CPU | 0.080 ms CPU | Same replay; the ordinary small-account saving is modest |
| Fully expired 50000-fill heap | 177.74 ms CPU | 143.94 ms CPU | Native old/current replay; shuffled identities at one expiry and identical final Decimal state |

Raw logs/probes are under ignored `build/flow-review/`. Millisecond observations
are evidence, not environment-sensitive unit-test thresholds or Cloud SQL
latency promises. The accepted daily query still uses six SQLAlchemy statements
and one repeatable-read snapshot; cash and fee arithmetic remain NUMERIC.

## 1. Bound and retain PostgreSQL readers

A dedicated owner connection remains pinned. With four overlapping readers, the
old pool repeatedly closed overflow and created a backend on later bursts. A
QueuePool simulation over 100 rounds created 104 connections, versus five with
the retained pool. Real PostgreSQL Red/Green tests reproduce the churn over twenty
rounds, require at most four additional connections after the warm owner, and
prove a sixth simultaneous checkout times out. No timing assertion is needed.

Retaining readers uses more idle backend resources; the maximum checkout ceiling
is unchanged. Pre-ping, connect/statement/lock timeouts and UTC settings remain.
Terminating an idle reader must reconnect that reader; terminating the dedicated
owner must still latch ownership loss and close submission permission, never
reconnect its lease in place. Thirty-two focused pool/ownership cases pass.

Commit: `d6fac719b`.

Regression: [reader pool and lost owner](../test/integration/postgres/test_runtime_pool.py).
The final image records five engine connection creations throughout all four
healthy load windows. Median sampled pool-checkout p95 is 2.59 ms in the first
five soak samples and 2.31 ms in the last five. These are observed local values,
not a new connection/latency limit or a comparison against a different image.

## 2. Daily aggregates avoid redundant conversion

The core `trade_fee` column is JSON. Native `json_array_elements` and JSON field
access avoid converting every row into JSONB before reading the same fee fields.
Event-day conversion avoids NUMERIC division of millisecond timestamps before
calling the timestamp function. Money, volume, actual-fee currency and rebate
sums still use their existing exact NUMERIC expressions.

Regressions cover 23/25-hour DST days, half-open millisecond boundaries, pre-epoch
instants and year-9998 boundaries, alongside existing percentage/flat fees,
missing conversions, late events, market separation and concurrent-fill tests.
There is no persistent rollup that could miss late fills or funding backfill.
Existing TTL/lock behavior and query budgets remain.

A trial materialized day-boundary join was slower: median 1202.36 ms and p95
1281.44 ms on the same 100000-fill fixture. It was rejected. The shipped simpler
query improves measured median by about 25% and p95 by about 20%; intermediate
faster samples are not substituted for the final idle measurement.

Commit: `ce22de4e3`.

Regressions: [daily accounting and time boundaries](../test/integration/postgres/test_admin_daily.py)
and [aggregate/query budgets](../test/integration/postgres/test_admin_reporting_optimization.py).

## 3. Narrow fresh reads and normalize observations once

The owner query retains the same backend PID, advisory-lock existence, owner UUID,
generation, current control/session and complete recovery checks. It omits the
optional published `status` JSON that no owner-gate caller consumes. The query
frequency and pre-intent/final-send authority checks remain unchanged.

Reporting calls select current control, heartbeat and publication fields without
private recovery JSON or the archived session catalogue. This is a fresh SQL
projection, not a TTL cache of authority. The full `read()` remains available to
control/recovery operations. A Red/Green regression specifically covers JSON
`null` as well as SQL NULL, so clearing a session through the ORM remains a valid
idle state.

The reporting capture now validates finite JSON while performing its existing
normalization; snapshot publication no longer repeats that validation pass.
Optional reporting failures retain the existing availability handling. Model,
UTC/Decimal serialization and response schemas are unchanged. The paired replay
shows a modest CPU saving; no unmeasured payload-latency percentage is claimed.

Commit: `7594b1784`.

Regression: [fresh state projections](../test/integration/postgres/test_state_read_projections.py).

## 4. Bound repeated heap work for whole-window expiry

A conservative upper bound tracks the latest expiry timestamp. Only when every
heap entry is expired and at least 1024 entries exist does the projection sort
and drain that heap once. Sorted order is exactly the previous repeated-heappop
order, preserving Decimal arithmetic order. Partial expiry retains the existing
heap path. Future risk and pending reservations are separate and preserved.

The new cost regression fails before the optimization and passes afterward. It
compares one large advance against incremental clock advancement, including
long decimal amounts, original-currency rebates, future debits and subsequent
re-use of the projection. A separate deterministic differential replay matches
the old implementation across ten seeds and 10000 actions: large clock jumps,
late/future fills, percentage-fee uncertainty, rebates and reservations.

Trade-offs measured in the same native replay: building the 50000-fill projection
rises from 260.06 to 273.01 ms CPU (about 5%); ordinary empty advance rises from
0.377 to 0.441 microseconds. The targeted gain is lower synchronous expiry cost,
not a claim that every projection operation is faster. Future/reservation
accounting and exact fill deduplication retain their existing rules.

Commit: `6aae523fc`.

Regression: [exact outflow state and bounded heap operations](../test/hummingbot/core/test_spot_outflow.py).

## Cache and query budgets

The existing process-local daily statistics TTL cache remains sufficient for
this workload. History SQL counts are bounded by page rather than row count:
orders at most three, fills/performance one, session events two and liquidity
one. The image load gate verifies these bounds and checks that FE polling does
not multiply exchange account reads. Owner fencing, HALT, recovery and current
control reads remain fresh. A shared Redis cache would add a dependency without
addressing the four measured costs above.

## Verification

The verified runtime tag is `hummingbot-cloudrun:optimized`. To apply the existing
BTC-USDT Demo preset to this runtime, use `make build-cloudrun-demo
CLOUDRUN_IMAGE=hummingbot-cloudrun:optimized`; the thin layer reuses the runtime.

Commands: `make test-cloudrun-regression`, `make test-postgres`,
`make swagger-check`, followed by `scripts/test_e2e_offline.py --scenario all
--trading-env demo --previous-image <compatible-previous-id>` and
`scripts/test_mm_load.py --profile all --enhancements`, both pinned to the same
optimized image ID. The E2E exchange is a local simulator even though its connector
protocol mode is explicitly `demo`. The separate fault retest uses
`scripts/test_mm_load.py --profile faults --enhancements` on that same image.

The load gate retains 1 CPU/2 GiB for the bot, 100000 historical orders/fills,
the original eleven-route 20 reads/s population, separate event/liquidity reads,
book/fill traffic, 300/900/90/3600-second warmup/mixed/burst/soak windows and five
fault windows. Healthy-window read p95/p99 limits remain 500/1000 ms with zero
unexpected errors; the sixty-minute RSS-growth limit remains 64 MiB. Tracing is
off, as in the production default. Feature guards are explicitly on in the fixture.

- [x] Native: 1571 tests plus eleven subtests; Swagger check passes. Two added cases
  cover simulator book/candle sends after transport closure. Existing dependency
  deprecation warnings remain.
- [x] Full PostgreSQL on the final runtime source: 204 cases pass.
- [x] Image `c89ce9122a39`, 1366522531 bytes, amd64: all 53 packaged paths match source.
- [x] All fourteen simulator scenario groups pass, including nine profile/market
  combinations and upgrade/takeover/rollback against compatible image `865cc81a5c05`.
  This previous image is the current base that had package/runtime smoke evidence;
  it is distinct from the historical E6 artifact.
- [x] All four healthy load windows pass their original budgets, including the
  unchanged sixty-minute zero-error gate.
- [x] All five fault windows pass in the separate fixture retest, with genuine
  simulated fills and retained positions in both markets.

| Healthy window | Seconds | Primary reads | p95 / p99 ms | Unexpected errors |
| --- | ---: | ---: | ---: | ---: |
| Warmup | 300 | 6000 | 60.73 / 393.64 | 0 |
| Mixed | 900 | 18000 | 76.61 / 289.48 | 0 |
| Burst | 90 | 1800 | 89.83 / 365.71 | 0 |
| Random soak | 3600 | 72000 | 84.25 / 461.65 | 0 |

The additional event/liquidity population completes 9780 reads with no unexpected
errors; it remains outside the original 97800-read latency population. Burst
backlog drains in 0.87 seconds. All three profiles are observed during the random
soak. Its fill/book rates are observed rather than fixed-rate acceptance gates,
as before, because joint-readiness gaps at profile transitions are intentional.
The final healthy ledger contains 4884 actual simulator fills and the same 4884
recorder fills, exact original-currency fees, matching positions and two identified
funding bills. No synthetic fill counter is substituted for exchange truth.

Soak median RSS growth is 10.07 MiB across 58 runtime samples, below 64 MiB. All 59
captured client minute-p95 samples are below 500 ms; the largest is 261.68 ms.
Complete-window percentiles above remain the acceptance measure. Runtime timing
distributions in the JSON retain bounded samples, not exclusive complete-window
CPU/SQL distributions.

## Fixture failure and separate retest

The first `--profile all` root result is **failed**, despite passing every healthy
window and four fault windows. After the injected DB delay, preparing the fifth
fault timed out on MM start. Logs show public-feed delivery stopped around the old
connection's shutdown and retained-position valuation subsequently became stale.
The simulator's market emitter did not catch a transport closing between its
`socket.closed` check and `send_json`; one failed send could terminate the shared
feed loop. Cleanup gathered that exception without logging it.

Two deterministic Red/Green cases reproduce that failure for book and candle
sends. The fixture fix retires only the failed socket and continues real delivery
to healthy subscribers; failed sends never increment the book counter. It does
not catch cancellation or arbitrary application errors. Production runtime/image
and healthy load budgets remain unchanged.

The subsequent standalone five-fault run seeds one actual simulator fill in each
market with duplicate delivery and verifies independent amounts/fees/positions.
Feed loss, owner restart, queued expiry, delayed DB and failed DB all pass,
including the recovery preparation that timed out before. Injected-fault HTTP
503/connection failures are classified separately from healthy errors. This is
combined evidence from separate scopes, **not a passing monolithic rerun**.

See [portable evidence](life-mm-runtime-optimization.json) for both raw result
paths, source/fixture hashes and the preserved failed-attempt status.

## Limits

Native microbenchmarks and an amd64 image under arm64 host emulation have different
scopes. No Cloud SQL latency or real OKX execution improvement is claimed. Existing
E6 Demo/image results remain historical evidence for their own image. Historical
cold traced stress failures are not retested or declared fixed by this untraced
workload. All optimization checks use local synthetic data; no Cloud Run deployment
or connected exchange session is started.

Fixture correction commit: `9d6538343`.
