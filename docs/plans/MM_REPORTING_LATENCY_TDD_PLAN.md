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
- [x] R1A (requested architecture experiment): retain **one Cloud Run service**;
  compare one process with two isolated API/engine processes in one container.
  Reuse existing handlers, durable command queue, ownership/fencing and engine.
  Keep the same total CPU/RAM and five database pool slots. First run isolated
  fake-exchange diagnostics and a controlled engine-loop stall. Verify that
  critical child exit cannot leave a healthy API container masking a dead bot.
  Prototype wrappers are test-only until measurements and lifecycle tests justify
  production adoption. Process isolation alone does not reduce the SQL cost.
  Decision: stall isolation verified (about 2.6s -> 0.1s), but both longer paired
  healthy diagnostics miss p99; keep production unchanged. See the
  [experiment](../../reports/mm-process-isolation-experiment.md).
- [x] R2 Red: cover last-good fallback, no fallback without a matching successful
  snapshot, stale age/clock/day rollover, transient versus permanent failures,
  concurrency, filter isolation, cancellation and recovery.
- [x] R2 Green: extend the bounded existing daily cache with a maximum 60s
  original-observation age; label stale data/retry hints in English. Live
  balances/positions and trading gates never consume the fallback. Update
  Swagger/FE docs, and make healthy load detect degraded daily responses; commit.
- [x] R2 follow-up: reproduce TTL expiry between lookup and eviction in two
  Red tests; use safe eviction, 55 focused Green tests. Rebuild candidate before
  final gates; the interrupted earlier load is explicitly superseded.
- [x] R3a: instrument submit-to-worker-start waiting with context-safe bounded
  test diagnostics; three Red / eighteen Green timing/trace/executor tests.
- [x] R3b: collect a traced 180s all-feature candidate probe with physical worker,
  SQL/pool, loop/GC and per-request timing. Probe passes; queue/pool expansion or
  a scheduler fix is not justified. Retain historical attribution uncertainty.
  Longer final-image gates below remain required; no short-probe substitution.
- [x] Verify corrected cache image `58356746…`: 1,940 native tests plus eleven
  subtests, 259 real PostgreSQL tests, Swagger/runtime/source checks and all
  sixteen offline E2E scenario groups, including compatible upgrade/rollback.
- [ ] Repeat unchanged load gates and production-entrypoint control on the
  final selected architecture/artifact. Preserve failed runs; stale fallback
  must not turn a degraded healthy load into a false pass. Deferred while the
  requested supervision candidate is evaluated, not waived.
- [x] Publish measured outcomes to date under reports/, update checklists and
  commit verified phases. Remaining acceptance is explicitly left open.

## Availability follow-up: single-service supervision

Operator clarification: extra RAM is acceptable. Prioritize responsiveness,
bounded long-running retention and automatic process recovery. The earlier
process experiment is not rejected for memory use; its healthy latency and
whole-container failure policy still require work. Keep one Cloud Run service.

- [x] R4 Red/Green: independently restart a failed API child without interrupting
  the engine; physically reap a failed engine before restarting it through the
  existing ownership/reconciliation path. Bound retry state and use capped
  backoff. Shutdown must disable restarts and terminate/reap both children within
  the platform budget. Detect sustained unresponsive loops without treating a
  normal guard wait or HALT as a process failure.
- [x] Exercise real child crashes/hangs with isolated PostgreSQL/fake OKX. Verify
  API recovery preserves engine ownership, session identity and live orders;
  engine recovery never resets HALT, resends uncertain orders or creates a
  second owner. Preserve genuine failed cases.
- [x] Compare 300s warmup / 900s supervised healthy load. Both fail latency;
  retain failures. Twenty memory samples show bounded-window stability only.
- [x] Trace follow-ups: 300s p99 433.70ms / corrected-probe 358.09ms, zero errors.
  Red/Green fixes a test-only heartbeat measurement race; 28 selected tests pass.
  GC/request overlap is partial and does not uniquely attribute the old spike.
- [ ] Establish repeatable healthy latency and continuous-hour memory on the
  final candidate; finish production port/pool/child-error/startup integration
  and final-image lifecycle/load checks before adoption. Extra RAM acceptable;
  unbounded growth, hidden degradation and relaxed budgets are not.

Process recovery does not authorize trading recovery. Current operator HALT
recovery still requires technical review; no reset/resume API is implemented.

## R6: production single-service supervision integration

- [x] Move the tested bounded child policy into runtime code; Red/Green for
  physical reaping, exit/termination races, fatal startup versus retryable
  failures, startup grace and shutdown without relaunching children.
- [x] Integrate the ordinary service entrypoint: public API / loopback engine
  health, configurable ingress port, shared total DB ceiling partitioned between
  roles, one migration owner, child dotenv isolation and no exchange credentials
  in the API environment. Reuse the current handlers, engine and PostgreSQL queue.
  Native integration: 95 selected tests pass. Runtime image and actual role
  lifecycle verification below remain required before adoption.
- [x] Cover API/engine health, initialization interruption, API-only restart,
  engine reconciliation/HALT and parent shutdown using production entrypoints.
  Update offline instrumentation to measure both real roles without replacing
  the shipping supervisor or masking missing metrics.
  Overlay probe passes actual-role faults at nondefault ingress 8090; final-image
  rerun remains required below. Native focused regression: 124 tests pass.
- [ ] Commit verified runtime phases; build and fingerprint the final image.
  Run native/real-PG regression, runtime smoke, Swagger and full offline E2E.
- [ ] Run unchanged workload/error/latency gates and continuous-hour per-role
  memory checks on the selected final image, followed by recovery faults and an
  ordinary-entrypoint control. Preserve failures, report scope, update checklist
  and commit. No real Demo replacement, trading start or Cloud Run deployment.

Real local Demo remains stopped. Load tests use isolated fake exchanges/DBs.
The [investigation](../../reports/mm-p99-investigation.md) retains its original
image scope; no single historical p99 trigger is assumed.

## R5: attribute the longer healthy latency failure

- [x] Red/Green: allow the same 300s warmup / 900s healthy workload with tracing,
  retaining each client's summary separately. Add request-ID correlation through
  the existing physical-worker, pool, SQL and response-model timing helpers;
  verify real HTTP/SQL success and failure, parameter redaction and hook cleanup.
- [x] Repeat that longer diagnostic with corrected heartbeat measurements and
  retained executed prototype sources. Attribute outliers to measured stages;
  preserve failures and trace overhead/scope limitations.
  Outcome: warmup p99 1861.62ms / healthy 2266.57ms, zero HTTP errors; both
  latency gates fail. Request stages identify SQL, worker and client backlog
  contributions; pool queue maximum is only 6.64ms in retained slow roots.
- [x] Compare the existing daily cache at 15s versus the current 5s, explicitly
  recording the policy and unchanged workload/budgets. A short diagnostic does
  not close the longer healthy/hour gate or the cold million-fill SQL limit.
  Outcome: 300s TTL15 p99 220.63ms; matched 300s/900s TTL15 p99
  631.47/206.92ms, zero errors. Demo environment example explicitly uses 15s;
  application default remains 5s. Sequential measurements are not a universal
  speedup guarantee. The longer run fails a later instantaneous quote assertion;
  its passing latency windows do not certify that incomplete fault sequence.
- [x] Red/Green for the recovery probe crossing normal sixty-second quote
  refresh: bounded wait, partial working orders and permanent-loss rejection;
  recheck identity/session after the wait. 59 native tests and a separate actual
  container fault suite with the forced refresh boundary pass. Retain the
  preceding long-run failure; final architecture/hour gates remain open.
- [x] Apply Red/Green to any confirmed runtime defect and select the next
  architecture/artifact from the evidence. Unproven causes do not justify a
  production change. Final unchanged load/hour-memory acceptance remains open.
  Decision: no new runtime defect is established by the latency pattern; retain
  production topology, evaluate the explicit 15s reporting policy, and keep
  independent supervision test-only until the R4 integration gates pass.

Daily fallback contract: keep the database observation timestamp unchanged;
return explicit English freshness/retry metadata. Retention is bounded to 32
filter/timezone/date-range keys. Both elapsed monotonic time and wall-clock
observation age must permit reuse; clock reversal, future observations and a
relative reporting-day rollover invalidate reuse. A short retry cooldown
coalesces simultaneous failed reads without extending the observation deadline.
Validation/schema/authentication errors and cancellation must never be hidden.
Disabling the existing daily cache also disables last-good fallback. Current
balances, positions, trading admission, ownership and controls do not use it.
