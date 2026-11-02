# Residual engine probes: observer query and executor reports

Date: 2026-11-02 (Asia/Saigon).
[Plan](../../docs/plans/MM_ENGINE_RESIDUAL_PROBE_INVESTIGATION.md).
Start from locally accepted image `e2d393b1` / application `a67233dc9`.
This follow-up confirms and fixes a duplicate observer query. Source and bounded
image diagnostics are complete; no new full release acceptance is claimed.
[Portable evidence](mm-engine-residual-probes-evidence.json).

## Confirmed duplicate and narrow fix

For an existing owner, observe_once calls try_acquire (which itself performs
check_owner) and immediately calls check_owner again. A final recovery check
later in the same stopped observer remains necessary. This gives three owner
queries, with the first result discarded.

The observer now calls try_acquire only when its ownership connection is absent,
then reads fresh owner/control state as before. Real PostgreSQL verifies two
queries instead of three in the stopped observer path. The saving is exactly one
round trip per iteration with an existing owner, not a 33% reduction in all bot
SQL or a general throughput claim. No snapshot/authority cache is introduced.
First acquisition, standby, lost/closed/invalidated connections, final recovery
reads, every per-order check and the final transport fence keep their behavior.

The unit Red shows three reads instead of two. A real PostgreSQL Red confirms
the same count. A stronger counterfactual uses the accepted source directly,
injecting SELECT pg_sleep(0.12) only into ownership reads. An already-started
external HTTP client uses the shipping 300ms timeout: three synchronous reads
on the accepted host cause TimeoutError; the candidate's two reads return 200.
The snapshot/publication path is mocked only to isolate these ownership reads.
This is a sufficient cause under the injected delay, not attribution of any
particular earlier recovered probe.

Five unit cases pass after the fix. Native regressions pass 2,284 cases plus 11
subtests. Full PostgreSQL passes 285; the subsequent focused query-count and
HTTP checks both pass (one repeats an existing case, one is an added case).
Swagger passes. An extra initial unit failure concerned admission-flag bookkeeping on an
already-lost owner: both paths reject ownership. That fixture uses admission
already closed before the retained one-failure/four-pass query Red; it is not
evidence that old code allowed an order.

## Executor report investigation

The retained final R1 distribution has typical executor-report costs around
2.3ms median and 6ms p99, with isolated maxima near 470ms. Those timings do not
support assuming a persistent hundreds-of-milliseconds report cost. The
closed-executor diagnostics array is not a resident-executor count and is not
used to infer an empty engine.

All retained healthy-envelope executor-report spans were reviewed, including
non-probe stall captures: three tails are 470.48/424.94/358.36ms with thread CPU
376.45/239.52/144.47ms. No retained GC event intersects those three spans; the
final full-GC event list also has no intersection. This does not rule out lost
context, allocator/runtime costs or host scheduling, and does not identify a
unique cause. These are client-side timings, not PostgreSQL server timings.

A native standalone profile exercises actual PositionExecutor, InFlightOrder,
TrackedOrder, ExecutorInfo and ExecutorOrchestrator logic with deterministic
in-process connector data. Each case has 100 warmups, 1,000 unprofiled reports,
and a separate 100-call cProfile pass. No network, DB, runtime recorder or actual
exchange connector is used; these are not production-throughput measurements.
The largest per-order fill count in the retained exchange fixture is 22, included
as a computational stress case. A changing mark still changes net PnL correctly
on the next report, including exact original quote-currency fees.

| Native report case | Median | p99 | Max | Local price reads/report |
| --- | ---: | ---: | ---: | ---: |
| 12 unfilled executors|0.168ms|0.214ms|0.407ms|0|
| 12 filled, 1 fill/order|0.635ms|0.818ms|1.180ms|36|
| 48 filled, 1 fill/order|2.468ms|16.965ms|51.099ms|144|
| 12 filled, 22 fills/order|2.306ms|3.211ms|5.781ms|36|

The 48-executor case has a larger native tail; all attempts are retained, not
replaced with a better repeat. Initial preliminary runs overlap broad native
regressions and are kept separately; the table uses the subsequent standalone
run. Initial helper failures use an invalid configured-maker order type and an
incorrect TrackedOrder constructor; correcting the fixtures runs real classes,
without changing application behavior.

There are repeated local price/property reads within a filled DTO, not a
measured N+1 REST/SQL query in this fixture. Existing code already constructs one
DTO per resident per report and reuses it for positions/performance. The
measured 12-executor costs and failed reproduction of the large Docker tail do
not justify another valuation cache or a second report implementation. Executor
runtime code remains unchanged. Retained traces and limited native profiles do
not exclude costs at other executor/fill counts or in real connector code.

## Image diagnostic and scope

Commit `717c51727` packages as candidate `18422eca` over accepted `e2d393b1`.
Shared dependency layers, all other application/config hashes, actual import
and metadata are verified. Only service.py changes. The LIFE MM image lifecycle
group passes all three profiles across spot/perpetual/both plus Stop/restart/DB
recovery. This is one targeted group, not every image E2E group.

A subsequent 300s ordinary-entrypoint run uses the accepted 100k-history fixture,
unchanged quotas/rates/TTL/budgets, fresh isolated PostgreSQL and synthetic
fixture metadata marked passed=false so it cannot borrow earlier acceptance.
All budgets pass: API p95/p99 49.30/61.20ms, zero unexpected API errors, 300 unique
fills with original fees and positions reconciled, two exact funding bills and
unchanged sampled supervisor/engine/API PIDs. Peak container memory is 673.10MiB;
this short peak does not establish long-term retention.

There are no engine probe transitions within the measured window. One API
probe fails and recovers (supervisor interval 1.065s, reported failed attempt
389.272ms). No child restart occurs. The test does not instrument runtime stacks,
so the API tail has no causal stack attribution. This shorter, single candidate
run cannot establish a universal improvement over earlier 600s/hour conditions.
Full R1/R2 and a new hour soak are not rerun in this investigation; image
`e2d393b1` remains the artifact with completed full gates. Candidate tag is
`hummingbot-cloudrun:observer-check`.

Raw diagnostic: `build/service-supervision/production-control-20261102-131912-dd6950`.
The temporary registry alias and owned test containers are cleaned; accepted and
candidate images are retained. Original Demo volume is present and was not
mounted. A read-only progress helper initially indexed an empty log; the guarded
reader fixes that without interrupting the independent image pipeline.
Ownership and executor tails remain investigation limits until further causal
evidence exists; no unique root cause for all 15/6 earlier episodes is claimed.

Raw Red/Green/profile/helper attempts: `build/engine-residual-20261102/`.
No Demo account/config/volume change or Cloud Run deployment is performed.
