# Mixed-window p99 investigation

2026-10-29. [Portable evidence](mm-p99-investigation-evidence.json).

Current follow-up status (2026-10-30): final-image local acceptance is complete
on `715a11f3…`, including unchanged hour/load/faults and matched ordinary control.
See [the accepted runtime report](../release/mm-service-supervision.md). The original
diagnostic observations below retain their original image scope. Exclusive
attribution of the original spike remains unknown; later passes do not supply
its missing trace or reclassify these failed diagnostics.

**The latency class is reproducible with tracing both enabled and disabled.**
The dominant observed flow is an event-loop/scheduling stall followed by server
and client queueing under the one-CPU, emulated synthetic workload. A separate
real PostgreSQL statement timeout was confirmed on daily fee aggregation.
**The unique trigger of the original 1,013 ms result remains unconfirmed.**
No runtime patch, new cache, pool increase or budget change is claimed.

## Completed investigation and remaining scope

- [x] Retain the original fail/pass evidence and distinguish rolling metrics from causal traces.
- [x] Complete matched traced/untraced 300s warmup + 900s fixed mixed diagnostics.
- [x] Correlate scheduled client reads, handler/SQL spans, GC, stalls and CPU quota counters.
- [x] Add targeted timings in a disposable test wrapper and run a separate 600s probe.
- [x] Replay CPU components and reuse the existing real-PG 100k-fill reporting benchmark.
- [x] Run the production entrypoint without the load wrapper for matched 300s/900s windows.
- [x] Publish failures, successful controls and measurement limits; preserve the real Demo service stopped.
- [x] Complete the later final-image healthy SLO follow-up on `715a11f3…` through
  its own E2E/hour/fault and ordinary-control gates; see the accepted report above.
  These original diagnostics do not substitute for those gates.

Known evidence limitation: the original historical failure has no retained trace
that establishes one exclusive source-level trigger. It is not an unfinished
implementation checklist item or a claim of resolved historical causality.

## Test matrix

All new cases use rebuilt amd64 image `143ddfe2…` on the ARM host. Its 29 selected
runtime hashes match the accepted source. The original `be3efe46…` binary was
removed by operator Docker cleanup; matching source does not prove binary identity.

Bot: one CPU / 2 GiB. PostgreSQL: two CPUs / 512 MiB. Client and simulator: one
CPU / 512 MiB each. Each seeds 100,000 historical orders and fills, offers 20 main
plus five auxiliary reads/s, 100 book updates/s and one synthetic fill/s, with
the same three-level enabled policies. Critical catalogue/client/exchange/fixture
hashes match across all four cases. All are explicitly diagnostic.

The workload intentionally uses **one-second synthetic candles** for defense
transitions. The packaged local Demo preset uses one-minute candles. The
existing completed-candle cache is active; approximately two NATR calls/s across
two markets are expected in this stress fixture, not evidence of a broken cache.
The one-minute preset's latency cannot be inferred directly from this workload.

| Case | Warmup main p99 | Mixed main p95 / p99 | Unexpected HTTP errors | Actual outcome |
|---|---:|---:|---:|---|
| Traced + GC timing | 785.98 ms | 283.83 / **1,817.24 ms** | 0 | Mixed main and auxiliary p99 fail |
| Untraced + GC timing | 699.66 ms | 459.19 / **2,044.06 ms** | **1 warmup 503**, zero mixed | Warmup error and mixed p99 fail |
| Production entrypoint, load wrapper absent | 523.27 ms | 166.06 / **659.99 ms** | 0 | Both measured windows pass |

The additional 600s targeted-timing probe records main p95/p99 294.79/966.81 ms,
auxiliary p99 **1,007.46 ms** and zero errors. It fails the unchanged auxiliary
latency gate; it is not a new mixed-window or release pass.

Every 300s/900s case independently reconciles cumulative 1,200 exchange/recorder
fills and positions, two identified funding bills and daily snapshot existence.
The plain entrypoint control omits internal query/loop/GC counters and held-adapter
introspection; exchange/recorder/position checks remain. It cannot approve a full
release. Its measured mixed average CPU is 0.366 cores; absence of the wrapper
alone does not prove a unique cause from one sequential control.

## Reproduced delay mechanism

The traced warmup contains **37/6,000** scheduled main reads above one second
(0.62%), while mixed contains **250/18,000** (1.39%). This explains how a window
can have individual multi-second requests and still pass p99, then fail when a
single correlated burst affects more than one percent of its population.
Unscheduled reconciliation/status control polls are excluded from these counts.

Of the 250 mixed outliers, **209 fall in minute 14**. In a 12.813s portion of that
burst, bot CPU usage is 10.605 CPU-seconds (0.828 cores on average), with 55 of
127 cgroup periods throttled and 2.739s reported throttling. Whole-window CPU
averages would hide this burst. These counters are observations, not exclusive
CPU attribution to any one function; throttling must not be added to span times.

The main-thread watchdog records up to **2,243 ms** lag. A representative earlier
cluster shows:

- `/performance`: 1,786.8 ms client latency; the last measured DB span finishes
  1,611.5 ms before the handler root finishes.
- `/status`: 1,759.1 ms client latency, including 1,623.6 ms before the handler.
- `/orders?limit=500`: 1,757.0 ms client latency, including 1,511.7 ms before the handler.

The worst mixed requests reach roughly 6.4–6.7s scheduled latency. Some spend
5.4–5.9s in the **client's ten-reader queue**, followed by a shorter HTTP request.
The client measures from scheduled time, correctly retaining backlog delay; its
five-second network timeout excludes time before HTTP dispatch. Thus zero HTTP
timeouts is compatible with a failed scheduled-latency budget.

Recorded pool queueing is small compared with these stalls: traced observed max
41.7 ms; the historical failing snapshot had p99 0.045 ms. This is the SQL pool,
not the separate API dispatcher's pending queue. The latter is not directly timed
here and cannot be ruled out completely. Query-count checks continue to pass:
orders at most three statements, fills/performance at most one. The isolated
daily report uses six statements. No page-size-dependent N+1 was established.

## What the additional timings rule out

GC contributes pauses but is **not a sufficient explanation for every burst**.
Only 42 of the 250 mixed outliers overlap any recorded GC event; 161 overlap
sampled lag intervals. The worst minute-14 burst has no large full GC during its
central CPU interval. The untraced mixed case records full-GC pauses up to
650 ms; an earlier untraced 180s probe recorded 1,061 ms. The original 900s failure
has no request/GC trace, so that earlier pause cannot be assigned to it.

Targeted in-window measurements:

| Synchronous stage | Observed maximum wall / thread CPU | Interpretation |
|---|---:|---|
| Metrics export | 112.23 / 101.86 ms | Measurable test overhead, insufficient alone for a two-second stall |
| Session validation | 212.37 / 208.16 ms | Includes an overlapping 209.15 ms main-thread GC |
| Valuation readiness | 33.98 / 22.61 ms | No measured second-long intrinsic cost in this probe |
| MM runtime readiness | 42.80 / 31.43 ms | Same limitation: one probe, not a universal bound |
| Direct JSON response encoding | 124.80 / 52.82 ms | Does not explain all tail delay |
| State read | 420.21 / 4.98 ms | Wall time includes DB/scheduling waits |
| Reference update | 1,070.44 / 228.78 ms | Mostly the candle sample: 1,049.56 / 207.90 ms; no overlapping GC |

The last row leaves non-CPU waiting/descheduling to distinguish; it is not proof
that NATR performs a full second of CPU work. A watchdog frame sampled in
`bounded_wait` at peak lag is likewise not proof that SQL occupied the preceding
stall. Async observer root CPU includes interleaved work; nested spans must not
be summed or presented as exclusive function profiles.

An isolated one-CPU amd64 replay of 41 metric families/298,650 synthetic samples
measures quantile aggregation median **67.29 ms**, JSON encoding **0.58 ms** and
validation of the captured 5,943-byte test session **1.02 ms**. It excludes the
live process/GC graph, I/O and concurrent work. It supports avoiding speculative
session caches; it is not an exact replay of the original stall.

## Confirmed daily-statistics timeout

The untraced warmup returns one `/statistics/daily` 503. Request ID
`480080ab027a472f9ed1d7f0f5006084` matches the application warning. PostgreSQL at
**04:48:55.473 UTC** confirms `canceling statement due to statement timeout` for
the daily original-currency fee aggregate using `json_to_recordset`, under the
unchanged **2,000 ms** statement limit. The service remains running.

This is a real reporting performance failure under the diagnostic workload,
separate from the original zero-error p99 failure. The existing error boundary
returns a typed 503 rather than killing the owner. A standalone real-PG benchmark
at the same PG CPU/memory limits passes the 100k-fill correctness/count checks:
11 samples, p50 **374.35 ms**, p95 **513.90 ms**, six SQL statements. This is one
selected test, **1 passed / 256 deselected**, not a new full PG regression.

Production `daily()` already sets transaction-local JIT off. Its standalone
JIT-off fill/fee aggregate plans execute in 290.06/321.84 ms. The deliberately
JIT-enabled baseline is slower, but reapplying that existing fix is not a new
solution to this failure. The standalone measurements exclude concurrent HTTP
load; they do not exclude occasional query CPU/I/O/scheduling contention.

## Conclusion and follow-up

The investigation establishes **slow requests compounded by queueing**: a transient service
stall/CPU burst delays handler admission or continuation, then queues compound
scheduled latency. Tracing is not the only condition under which it occurs.
GC, synchronous runtime work, DB waits and test observation contribute different
costs; none has been isolated as the single trigger of the original event.
One passing production-entrypoint control shows that the stricter load wrapper
is not the production service, but does not statistically isolate overhead or
prove the production service cannot have the same tail.

No correctness defect requiring a runtime Red/Green patch was established.
There is consequently **no runtime fix claim**, no new Redis/in-memory cache,
no relaxed deadline/latency threshold and no weakened ownership/recorder ordering.
The reporting timeout and stable SLO repeatability remain performance follow-ups.
The next discriminating measurements are API submit-to-worker-start queue time
and a CPU profile/native-amd64 comparison during a reproduced burst, followed by
TDD for any confirmed code fix. Native deployment remains deferred by the user.

The historical full gate remains attached to its original image; these failures
must not be relabeled as passes or treated as fresh release acceptance. Raw runs,
helper hashes, observed loss counters and successful controls are indexed in the
portable evidence. Two production-control setup attempts never entered a measured
window (missing alias, then interrupted correction); both are retained separately.
The traced case also has twelve pre-window `SubmissionBlocked` order-failure logs
during scripted Stop; those are fencing rejections, not twelve successful orders
or twelve HTTP errors. No unrelated fatal service failure was observed.

All disposable test containers are cleaned up. The real local Demo service and
its persistent PostgreSQL remain running, with the trading session stopped; no
Start/Switch/Stop/trading command was sent to that real account during this work.
