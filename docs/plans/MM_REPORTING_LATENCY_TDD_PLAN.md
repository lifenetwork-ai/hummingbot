# Reporting latency and bounded fallback — TDD

Accepted scope: reduce the confirmed daily fee aggregation cost, preserve a
bounded explicitly stale daily snapshot on transient read failure, and measure
API worker admission before changing runtime scheduling. Reuse the current
PostgreSQL/API/cache/executor/test harness. No new trading engine, ledger, Redis,
pool expansion, relaxed deadlines or API edits to YAML.

Local acceptance completed on 2026-10-30 for image `715a11f3…` / manifest
`7e104887…`. Final image/regression/E2E/lifecycle, unchanged all-load/hour/faults
and matched ordinary-entrypoint control pass. Earlier decisions/failures below
retain their original artifact scope. No Cloud Run deployment or real Demo
replacement is included; cold million-fill SQL cost remains unchanged.

Operating priority clarified by the operator on 2026-10-30: slower historical
and daily aggregation is acceptable. Million-fill reporting latency alone is
not a delivery blocker or a requirement for further SQL optimization. Reporting
work must remain bounded and preserve engine feed processing, order/cancel
handling, ownership, recorder and recovery behavior. An optional report failure
alone must not stop trading; mandatory DB/ownership failures retain their safety
gates. Report timeout/staleness must remain visible to the UI.

The selected runtime separates API/engine processes and pools (two/three slots
at the default total five), but still shares CPU and PostgreSQL resources.
Acceptance therefore depends on measured engine behavior under reporting load,
not on a claim of unlimited isolation. Existing timeouts and admission limits
remain in place. The passing 100k-history gate and standalone million-fill SQL
benchmark retain their distinct scopes; this clarification does not reclassify
historical failures or certify a million-fill concurrent engine/load gate.

- [x] R1: Compare existing/candidate SQL on 100k/1M fills, repeated and diverse
  original-currency fees; preserve exact paid/rebate/net amounts, volume, missing
  conversion, pair/market/day/timezone and late-fill semantics. Retain candidates
  only with measured improvement and explain plans; commit the verified phase.
  Decision: reject all candidates after workload-dependent regressions; existing
  SQL retained. The confirmed timeout is not declared fixed. See the
  [report](../../reports/investigations/mm-reporting-latency.md).
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
  [experiment](../../reports/investigations/mm-process-isolation-experiment.md).
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
- [x] Repeat unchanged load gates and production-entrypoint control on the
  final selected architecture/artifact. Preserve failed runs; stale fallback
  must not turn a degraded healthy load into a false pass.
  All-load `20261030-054619-495f64` and matched ordinary control
  `production-control-20261030-072728-027167` pass on selected image715a.
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
- [x] Establish repeatable healthy latency and continuous-hour memory on the
  final candidate; finish production port/pool/child-error/startup integration
  and final-image lifecycle/load checks before adoption. Extra RAM acceptable;
  unbounded growth, hidden degradation and relaxed budgets are not.
  Local final-image gates pass: hour p99 657.95 ms / zero errors and stable role
  PIDs; ordinary-entrypoint mixed900 p99 499.00 ms / zero errors. See R6/R7 and
  [retained evidence](../../reports/release/mm-service-supervision-evidence.json).

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
- [x] Commit verified runtime phases; build and fingerprint the final image.
  Run native/real-PG regression, runtime smoke, Swagger and full offline E2E.
  Selected image `f5e7ab32…` / manifest `cee15b5a…`: 2,012 native tests plus
  eleven subtests; real-PG coverage completed across retained full/targeted runs;
  all sixteen E2E groups including compatible upgrade/rollback pass. Actual
  shipping-role crash/hang replay passes with nondefault ingress and clean
  3.28s parent shutdown. Both preceding full E2E failures remain recorded.
- [x] Resolve the late-fill checkpoint race found by long run
  `20261030-013705-7a6a10`: terminal-read-before-position-store with Red/Green,
  exact amount/fees recovery, continued HALT for unknown/post-checkpoint changes,
  and a deterministic cancellation-fill replay on the replacement shipping image.
  The preceding `f5e7…` image validation remains scoped to that image; its load
  acceptance failed and the new runtime needs full image/regression/E2E checks.
  Fixed candidate `80a34c69…` / source `c2a22c0ae` passes deterministic shipping
  replay at `20261030-025503-780bad`: exact spot/perpetual amount/fees, duplicate
  trade events, random rollover, clean cold restart and Stop. Full PostgreSQL:
  266 pass. Nine packaged runtime-module hashes match the committed source.
- [x] Complete all sixteen E2E groups, compatible upgrade/rollback and actual-role
  crash/hang replay on replacement image `80a34c69…`; prior `f5e7…` functional
  results are retained but do not certify the replacement.
  Replacement full16 `20261030-032310-8526be` passes, including exact cancellation
  fills and compatible rollback. Shipping roles pass at `20261030-025652-fcd9de`.
  The earlier held-ACK full failure stays recorded; its corrected fault-isolation
  replay holds ACK 976ms and keeps the same runtime timeout/HALT policy.
- [x] Run unchanged workload/error/latency gates and continuous-hour per-role
  memory checks on the selected final image, followed by recovery faults and an
  ordinary-entrypoint control. Preserve failures, report scope, update checklist
  and commit. No real Demo replacement, trading start or Cloud Run deployment.
  Selected image715a passes all-load/hour/six faults at `20261030-054619-495f64`
  and matched ordinary control `production-control-20261030-072728-027167`.

Real local Demo remains stopped. Load tests use isolated fake exchanges/DBs.
The [investigation](../../reports/investigations/mm-p99-investigation.md) retains its original
image scope; no single historical p99 trigger is assumed.

## R7: bound API full-GC startup scanning after a retained burst failure

- [x] Retain full load `20261030-040040-b6f2b7` as failed: warm300/mixed900
  pass, burst90 primary/auxiliary p99 1152.93/1120.29ms exceed unchanged limits,
  zero HTTP errors. API full-GC 1032.59ms co-occurs with loop/worker delays;
  this does not uniquely attribute the historical failures. Hour/control stay open.
- [x] Measure startup imports separately: ordinary API about 270k tracked
  objects; wrapper imports add about 8k. Do not blame all GC time on wrapper
  imports or change trading GC from this observation.
- [x] Red/Green candidate: collect import garbage and exclude the API's fixed
  startup graph from later full scans exactly once, before constructing its
  host/DB/pools/request workers. Preserve automatic runtime GC and thresholds;
  repeated preparation cannot freeze later request graphs. Test failed startup,
  startup ordering and reclamation of 5,000 later cyclic objects in an isolated
  process. Engine/parent GC and all trading/storage safety gates stay unchanged.
- [x] Compare retained old/new image diagnostics, including a traced burst and
  per-role GC policy counts, then repeat the untraced burst without lowering
  budgets or omitting reads. Isolated static-GC timing is not HTTP acceptance.
  Both untraced short bursts pass: old p95/p99224.63/539.08ms, candidate
  76.17/455.47ms, zero errors. Both traced diagnostics fail and remain recorded;
  lower API GC does not eliminate their other SQL/worker/client delays.
- [x] Build/fingerprint the selected replacement, complete native/real-PG,
  runtime smoke, Swagger/full16 E2E and shipping-role lifecycle checks.
  Image `715a11f3…` / manifest `7e104887…`: ten runtime hashes match; native
  2,030 plus eleven subtests, PostgreSQL 266/266, full16 E2E
  `20261030-050536-45e61f` and actual-role recovery
  `20261030-050256-89339d` pass. Packaged post-startup GC reclaims 5,000 cycles.
- [x] Require the unchanged all-feature all-load/hour/fault gate on this image.
  `20261030-054619-495f64` passes warm300/mixed900/burst90/soak3600 and all six
  fault cases. Healthy windows have zero errors; soak p95/p99 144.46/657.95 ms,
  58 samples, stable role PIDs, maximum gap66s and bounded retention. Each of the
  three profiles runs; 4,884 unique fills and positions match the recorder.
- [x] Complete matched ordinary-entrypoint control on this image before closing
  R4/R6 adoption; the instrumented all-load alone does not waive this control.
  First attempt `production-control-20261030-071800-602ad9` remains failed:
  harness assumed burst-only command timings existed in a nonburst window.
  Eight behavioral Red failures / thirty focused Green tests preserve actual
  setup-command measurements and unchanged missing/slow-command gates. Runtime,
  workload fixtures and passing baseline are unchanged; full control replay required.
  Corrected full replay `production-control-20261030-072728-027167` passes
  warm300 p95/p99 62.65/421.53 ms and mixed900 p95/p99 93.12/499.00 ms, zero errors and no
  budget violations. Image/caps/TTL and five client/exchange/catalogue fixture
  hashes match the passing all-load baseline; 1,200 unique fills/positions match.

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
