# Runtime flow optimization

Latest follow-up (2026-11-01): all three ordered source changes have targeted
Red/Green verification; broad regression and packaged verification are running.
See [implementation progress](#2026-11-01-ordered-implementation) and
[new evidence](runtime-flow-remediation-20261101.json). The original
[read-only review](#2026-11-01-follow-up--review-only) stays separate. Accepted
image `5973410d` and its R1/R2 results do not certify these new source changes.

Previous implementation status: runtime optimization complete; native/PostgreSQL regression, image E2E,
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

Regression: [reader pool and lost owner](../../test/integration/postgres/test_runtime_pool.py).
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

Regressions: [daily accounting and time boundaries](../../test/integration/postgres/test_admin_daily.py)
and [aggregate/query budgets](../../test/integration/postgres/test_admin_reporting_optimization.py).

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

Regression: [fresh state projections](../../test/integration/postgres/test_state_read_projections.py).

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

Regression: [exact outflow state and bounded heap operations](../../test/hummingbot/core/test_spot_outflow.py).

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

## 2026-11-01 follow-up — review only

This follow-up inspects order admission/cancellation, outflow reconciliation,
executor reports, controller lookup, status publication and API/DB reporting.
It uses current source at `60d8b6a0c` and isolated native replays. No connector
actor, connected Demo, database workload, runtime source, schema or image changes.
The earlier passing gates remain tied to their recorded source/image; this audit
is not an additional release test or a claim that every flow is optimal.

### Priority 1: terminal proof admission can starve later orders

[`SpotOutflowGuard.reconcile_terminal`](../../hummingbot/core/spot_outflow_guard.py#L216)
always takes the first four entries from `terminal_pending`, a set. Failed or
incomplete proofs remain in that set. With stable membership, repeated calls can
keep selecting the same four entries while other independently verifiable orders
receive no attempt. This is an in-memory admission fairness defect, not a SQL
N+1 query or a demonstrated service crash.

The actual method is reproduced with nine locally terminal orders and fixed fake
proof responses: the first four return incomplete, the other five could succeed.
After ten polling rounds, all40 proof attempts belong to the same four orders;
none of the other five is attempted. All nine reservations remain. This can delay
release of verified reservations and unnecessarily constrain further spot BUYs.
It does not bypass limits or prove incorrect accounting, and has not been
observed on the connected Demo in this review.

Recommended narrow fix: fairly rotate bounded proof admission while retaining
the four-attempt cap, exact order/fill proof, rate limiting and all reservations
until successful durable confirmation. No general cache or parallel request
fan-out is required. Red/Green cases should cover more than four pending orders,
permanent failures ahead of successes, insertion/removal during polling,
cancellation, and the unchanged `all_orders=True` incomplete-proof failure.
The isolated reproduction does not replace those implementation tests.

### Priority 2: reuse executor DTOs within one report call

[`get_all_reports`](../../hummingbot/strategy_v2/executors/executor_orchestrator.py#L902)
rebuilds executor information during hold processing, executor reporting and
performance reporting. For active executors this reads `executor_info` three
times per report; terminal paths can read it more often. Each property access
constructs a Pydantic DTO and recomputes PnL/fee/order-derived properties.

A paired100-round replay uses real `PositionExecutor` properties and actual
in-flight partial-fill models with fixed lightweight fake prices/rules. A
prototype reuses each DTO only within one synchronous report call. Complete
outputs match; no memo survives the next call. The production source is unchanged.

| Resident executors | State | Current median CPU | Per-call prototype | DTO reads |
| --- | --- | --- | --- | --- |
| 8 | Unfilled | 0.310ms | 0.123ms | 24 → 8 |
| 8 | Partially filled | 0.848ms | 0.313ms | 24 → 8 |
| 40 | Unfilled | 1.453ms | 0.527ms | 120 → 40 |
| 40 | Partially filled | 4.148ms | 1.463ms | 120 → 40 |

This is about60–65% less CPU **for this report computation**, not the whole engine.
The eight-executor partial-fill saving is about0.535ms per call. Fake prices,
no fill-arrival interleaving and no DB/network limit the comparison; it does not
explain historical multi-second probe episodes. TDD before implementation must
preserve POSITION_HOLD transitions/deduplication, active-to-terminal behavior,
fees and failed/stale marks, and prove that the next call sees changed fills and
prices. Keep final ownership and send-time checks fresh. Do not add a TTL cache
to executors or reuse a report across independent control ticks.

### Priority 3: avoid rebuilding configs merely to identify controllers

[`ServiceHost._mm_controllers`](../../hummingbot/cli/service.py#L124) invokes
`catalogue.controller_configs` on each lookup. In both-market mode that builds
two full `LifeMMConfig` objects just to obtain controller IDs. Readiness, quote
validation and status publication call this selector repeatedly.

On the checked YAML,1000 valid-case samples take median0.0925ms for the current
selector versus0.00129ms to select already-known IDs and verify the controller
set. The direct-ID prototype deliberately omits full validation; it is **not** a
behavior-equivalent replacement. Actual CPU benefit must be measured after
preserving validation at session/config admission and rejecting wrong/missing
markets, stale session/generation and changed controller identity. Do not skip
these checks or cache authority to obtain the prototype timing.

### Lower priority and work that should stay unchanged

- Status publication normalizes reporting JSON and later serializes the whole
  snapshot again. A future single normalization boundary could reduce allocations,
  but must retain finite-value checks, Decimal/UTC output and last-good observation
  isolation. No new measured saving is claimed here; ordinary account payloads
  are small, so this follows the three items above.
- Command observation uses an UPDATE and SELECT even when no receipt remains
  pending. There is already a matching status/version index and this occurs on
  observer publication, not per fill. Only consider combining statements after
  measuring actual round-trip cost; retain atomic supersession and truthful
  completion. Caching pending-command state is not recommended.
- API orders use grouped fill/side queries per page; fills use one paginated
  query. This audit finds no per-row SQL N+1 in those paths. Keep the existing
  query-count regressions rather than adding an unmeasured cache.
- Daily statistics already have bounded last-good TTL storage and serialized
  cache misses. Large cold aggregates remain a documented limitation. The
  operator accepts slower reports provided trading remains protected; no new
  rollup, Redis dependency, index or speculative SQL rewrite is justified here.
- Preserve current ownership/pre-send fencing, exact fill/fee deduplication,
  failure/HALT behavior, fresh balance/position checks and startup recovery.
  The existing resident-executor/order-ID pruning already bounds that history;
  do not replace it with a TTL that could lose deduplication guarantees.

Raw isolated experiment outputs are under `build/runtime-flow-review-20261101/`;
the [portable evidence](runtime-flow-review-20261101.json) records measurements,
source hashes and limits. No fix, production speedup, new memory bound or new
release acceptance is claimed for this read-only follow-up.

## 2026-11-01 ordered implementation

- [x] **1. Terminal-proof fairness:** Red7 failures/1 pass before the fix;
  Green26 native and13 real PostgreSQL outflow cases. Retry identities use one
  insertion-ordered dictionary, rotate before awaiting, and preserve the existing
  four-attempt cap and complete `all_orders` proof requirements. Removed entries
  are skipped; cancellation/failure retains pending obligations. The complete
  order/fill proof and reservation-release rules are unchanged.
- [x] **2. Executor report DTO reuse:** Red3 failures/2 passes; Green103 native
  and11 PostgreSQL drawdown cases. One per-call DTO list feeds hold transitions,
  output and performance. No memo is stored on executors or the orchestrator;
  independent calls and stale-price errors stay fresh. Tests cover active-to-held
  transitions, fee/PnL equality, deduplication, legacy reports, failure/retry and
  standalone callers. Paired actual old/new code gives0.867 →0.320ms for eight
  partially filled executors (24 →8 DTO constructions), and4.160 →1.467ms for40.
  This is report CPU on fixed fake prices, not whole-bot throughput or SLO evidence.
- [x] **3. Controller selection:** Red9 failures/8 passes; Green70 native and23
  PostgreSQL service/session cases. Fresh IDs come from the already validated
  catalogue; profile/mode/membership/combined budget and exact controller-set
  checks remain. Full config/metadata admission stays in the original factory,
  including saved legacy custom references. Session/generation, exposure and
  current collateral checks are unchanged. Paired actual old/new lookup gives
  0.0935 →0.00313ms over1000 valid both-market pairs, with zero new config models
  on that identity-only path. This is a local CPU microbenchmark, not admission
  equivalence on arbitrary unvalidated data or a whole-engine performance claim.

The regression Makefile explicitly includes the new fairness and report-snapshot
tests; controller-selection tests are discovered with the CLI suite. Full native/
PostgreSQL regression and new packaged targeted checks are running separately.

[Implementation evidence](runtime-flow-remediation-20261101.json) is separate
from the earlier read-only measurements. No connected account or Demo container
is changed. A newly built runtime needs its own image verification; historical
image `5973410d` keeps its accepted results and original source hashes.
