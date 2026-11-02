# Residual engine probes: observer query and executor reports

Date: 2026-11-02 (Asia/Saigon).
[Plan](../../docs/plans/MM_ENGINE_RESIDUAL_PROBE_INVESTIGATION.md).
Start from locally accepted image `e2d393b1` / application `a67233dc9`.
This follow-up confirms and fixes a duplicate observer query. Image diagnostics
are pending; it does not yet create a new full release acceptance.

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
on the accepted host cause TimeoutError; the candidate's two reads return200.
The snapshot/publication path is mocked only to isolate these ownership reads.
This is a sufficient cause under the injected delay, not attribution of any
particular earlier recovered probe.

Five unit cases pass after the fix. Native regressions pass 2,284 cases plus11
subtests. Full PostgreSQL passes285; the subsequent focused query-count and
HTTP checks both pass (one repeats an existing case, one is an added case).
Swagger passes. A first unit fixture incorrectly combined lost=true with an
open admission flag; it was corrected to the valid lost-owner invariant before
the retained one-failure/four-pass Red. It is not labelled a production bug.

## Executor report investigation

The retained final R1 distribution has typical executor-report costs around
2.3ms median and6ms p99, with isolated maxima near470ms. Those timings do not
support assuming a persistent hundreds-of-milliseconds report cost. The
closed-executor diagnostics array is not a resident-executor count and is not
used to infer an empty engine.

All retained healthy-envelope executor-report spans were reviewed, including
non-probe stall captures: three tails are470.48/424.94/358.36ms with thread CPU
376.45/239.52/144.47ms. No retained GC event intersects those three spans; the
final full-GC event list also has no intersection. This does not rule out lost
context, allocator/runtime costs or host scheduling, and does not identify a
unique cause. These are client-side timings, not PostgreSQL server timings.

A native standalone profile exercises actual PositionExecutor, InFlightOrder,
TrackedOrder, ExecutorInfo and ExecutorOrchestrator logic with deterministic
in-process connector data. Each case has100 warmups,1,000 unprofiled reports,
and a separate100-call cProfile pass. No network, DB, runtime recorder or actual
exchange connector is used; these are not production-throughput measurements.
The largest per-order fill count in the retained exchange fixture is22, included
as a computational stress case. A changing mark still changes net PnL correctly
on the next report, including exact original quote-currency fees.

| Native report case | Median | p99 | Max | Local price reads/report |
| --- | ---: | ---: | ---: | ---: |
|12 unfilled executors|0.168ms|0.214ms|0.407ms|0|
|12 filled,1 fill/order|0.635ms|0.818ms|1.180ms|36|
|48 filled,1 fill/order|2.468ms|16.965ms|51.099ms|144|
|12 filled,22 fills/order|2.306ms|3.211ms|5.781ms|36|

The48-executor case has a larger native tail; all attempts are retained, not
replaced with a better repeat. Initial preliminary runs overlap broad native
regressions and are kept separately; the table uses the subsequent standalone
run. Initial helper failures use an invalid configured-maker order type and an
incorrect TrackedOrder constructor; correcting the fixtures runs real classes,
without changing application behavior.

There are repeated local price/property reads within a filled DTO, not a
measured N+1 REST/SQL query in this fixture. Existing code already constructs one
DTO per resident per report and reuses it for positions/performance. The
measured12-executor costs and failed reproduction of the large Docker tail do
not justify another valuation cache or a second report implementation. Executor
runtime code remains unchanged. Retained traces and limited native profiles do
not exclude costs at other executor/fill counts or in real connector code.

## Scope and next verification

Package the single service.py change over the accepted image, verify source and
base layers, then run targeted MM lifecycle E2E and a bounded ordinary-entrypoint
load diagnostic. These checks are separate from a full release/hour-soak claim.
Ownership and executor tails remain investigation limits until further causal
evidence exists; no unique root cause for all15/6 earlier episodes is claimed.

Raw Red/Green/profile/helper attempts: `build/engine-residual-20261102/`.
No Demo account/config/volume change or Cloud Run deployment is performed.
