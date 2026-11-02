# Engine probe latency follow-up

Date: 2026-11-02 (Asia/Saigon).

Prioritize trading responsiveness. Reuse the current engine, ownership fencing,
recorder and offline load harness. The accepted application image is `a190b024`;
its release result remains unchanged by diagnostic changes.

- [x] Review existing probe incidents and identify confirmed evidence gaps.
- [x] Red/Green bounded retention of slow spans after a root reaches its child
  limit, including stable parentage and explicit dropped-span counts.
- [x] Red/Green reserved incident-file capacity for probe/loop/GC evidence;
  preserve the total byte ceiling, append/restart bounds and loss counters.
- [x] Add scalar ownership/fill/checkpoint phase timing to the existing test
  wrapper; preserve method behavior and avoid arguments or private state.
- [x] Run focused tracing/timing regressions and a bounded local image probe;
  verify independent fill/fee/position accounting and diagnostic limits.
- [x] Publish measured results and unresolved attribution, update progress and
  commit. Implement a runtime fix only if a specific defect is reproduced with
  a failing regression; safety checks must remain fresh.

The probe is diagnostic, not a replacement hour gate or a new production release.
Persistent Demo state/configuration and exchange accounts are outside this task.

Detailed high-frequency outflow advance spans are created only for completed calls >=100ms; bounded scalar timings still include every call. Watchdog stacks and enclosing spans cover in-progress work. This reduces diagnostic overhead; it does not change runtime GC or trading policy.

Result: [report](../../reports/investigations/mm-engine-probe-latency.md) and
[portable evidence](../../reports/investigations/mm-engine-probe-latency-evidence.json).
Selected native61, packaged smoke and300s probe pass (p99 157.84ms, zero errors,
300 matching fills/fees/positions). One transient probe failure recovers; its
unique cause remains open. Runtime logic is unchanged. External Docker cleanup
removed the previous bot container/image; the Demo DB volume remains untouched.

## Controlled follow-up after reporting/startup release — 2026-11-02

Baseline: accepted local image `39198499`, application source `9db4ff56a`.
Earlier diagnostic and release evidence above retains its original scope.

- [x] A: Analyze all54 recovered probe transitions in the existing R1 healthy
  envelope, distinguish measured windows from their gaps and retain attribution
  limits, GC/stall context and data-loss counters. Commit the evidence review.
- [ ] B: Reuse the existing shipping-control harness for matched600s diagnostic
  runs: ordinary entrypoint, scalar measurement wrapper, then wrapper plus GC
  and bounded incidents. Preserve image, fixed profile, fixture hashes,100k
  history, rates, CPU/memory, TTL, pool and all healthy/error/ledger budgets.
  Diagnostic variants must never qualify as shipping R2 or full acceptance.
- [ ] C: Evaluate the comparison; reproduce any confirmed shipping defect with
  Red/Green before a runtime fix. Rerun a full release gate only if shipping
  runtime changes. If no defect is reproduced, publish the bounded result and
  remaining uncertainty rather than speculate or relax health deadlines.
- [ ] D: Run current-image OKX Demo preflight with authenticated REST/public and
  private WS reads, balances, orders/positions and available recovery state.
  Preserve private configuration and original Demo volume; no trading commands,
  account changes, migrations or direct maintenance writes. State unavailable
  local recovery separately if the old container/DB is absent.
- [ ] Publish scoped evidence/report, update current status/checklists, commit
  and remove only task-owned transient resources/awake assertions.

Follow-up: [report](../../reports/investigations/mm-engine-probe-controlled.md),
[evidence](../../reports/investigations/mm-engine-probe-controlled-evidence.json).
No probe timeout/grace, ownership, order authority, quote/risk rule or cache/pool
limit is changed merely to make a diagnostic pass.
