# Reporting latency and bounded fallback — TDD

Accepted scope: reduce the confirmed daily fee aggregation cost, preserve a
bounded explicitly stale daily snapshot on transient read failure, and measure
API worker admission before changing runtime scheduling. Reuse the current
PostgreSQL/API/cache/executor/test harness. No new trading engine, ledger, Redis,
pool expansion, relaxed deadlines or API edits to YAML.

- [x] R1: Compare existing/candidate SQL on 100k/1M fills, repeated and diverse
  original-currency fees; preserve exact paid/rebate/net amounts, volume, missing
  conversion, pair/market/day/timezone and late-fill semantics. Retain candidates
  only with measured improvement and explain plans; commit the verified phase.
  Decision: reject all candidates after workload-dependent regressions; existing
  SQL retained. The confirmed timeout is not declared fixed. See the
  [report](../../reports/mm-reporting-latency.md).
- [ ] R2 Red: cover last-good fallback, no fallback without a matching successful
  snapshot, stale age/clock/day rollover, transient versus permanent failures,
  concurrency, filter isolation, cancellation and recovery.
- [ ] R2 Green: extend the bounded existing daily cache with a maximum 60s
  original-observation age; label stale data/retry hints in English. Live
  balances/positions and trading gates never consume the fallback. Update
  Swagger/FE docs, and make healthy load detect degraded daily responses; commit.
- [ ] R3: instrument submit-to-worker-start waiting with context-safe bounded
  test diagnostics; profile reproduced bursts before any runtime scheduling
  change. Confirmed fixes require Red/Green; otherwise retain uncertainty.
- [ ] Verify regression, real PostgreSQL, schema and final image. Repeat the
  unchanged load gates and production-entrypoint control. Preserve failed runs;
  stale fallback must not turn a degraded healthy load into a false pass.
- [ ] Publish measured results under reports/, update checklists and commit.

Real local Demo remains stopped. Load tests use isolated fake exchanges/DBs.
The [investigation](../../reports/mm-p99-investigation.md) retains its original
image scope; no single historical p99 trigger is assumed.

Daily fallback contract: keep the database observation timestamp unchanged;
return explicit English freshness/retry metadata. Retention is bounded to 32
filter/timezone/date-range keys. Both elapsed monotonic time and wall-clock
observation age must permit reuse; clock reversal, future observations and a
relative reporting-day rollover invalidate reuse. A short retry cooldown
coalesces simultaneous failed reads without extending the observation deadline.
Validation/schema/authentication errors and cancellation must never be hidden.
Disabling the existing daily cache also disables last-good fallback. Current
balances, positions, trading admission, ownership and controls do not use it.
