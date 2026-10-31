# MM tracing and extended load review — 2026-10-26

The five-minute warmup and fifteen-minute mixed window **pass**. The separate
sixty-minute random-session soak **fails**: p99 is **1,250.96 ms** against a
1,000 ms budget, with **10 client timeouts**. The long-run acceptance remains
open. Persistence, position reconciliation and resource-growth checks passed.

[Portable measurements and trace excerpts](life-mm-trace-load-evidence.json)
retain successful and failed windows. No production logic or cache changed.

## Results with unchanged budgets

| Window | Actual duration | Reads completed | p95 ms | p99 ms | Client errors | Result |
|---|---:|---:|---:|---:|---:|---|
| Cold diagnostic smoke | 60 s | 1,200 | 668.67 | 1,041.87 | 0 | Fail: latency and end backlog |
| Warmup | 300 s | 6,000 | 160.81 | 630.41 | 0 | Pass |
| Fixed mixed | 900 s | 18,000 | 158.88 | 830.72 | 0 | Pass |
| Random soak | 3,600 s | 72,000 | 345.91 | 1,250.96 | 10 | Fail: errors and p99 |

All windows offered 20 reads/s, 100 aggregate book updates/s and one synthetic
fill/s against 100,000 historical orders and 100,000 historical fills. Bot:
1 CPU/2 GiB; PostgreSQL: 2 CPUs/512 MiB; client and simulator: 1 CPU/512 MiB each.
Original budgets remain p95 ≤500 ms, p99 ≤1,000 ms and zero unexpected errors.
`--keep-going` retained every healthy window after budget failures, while the
final runner still exited nonzero for the failed soak.

Soak observations:

- 72,000 read attempts completed, including 10 timeouts; 71,990 returned HTTP 200.
  Achieved completion rate was 19.998/s, with 9 queued reads at generation end.
- Book rate was 99.972 updates/s. All 3,594 unique fills and exact fees matched
  the independent exchange ledger and PostgreSQL; perpetual positions matched.
  Both identified funding bills and daily fill counts matched; daily snapshots existed.
  Synthetic fills pause around scheduled handovers, so the final observed fill
  rate is 0.9983/s. The fixed mixed window enforced the sustained-rate budgets.
- 58 minute samples were collected; existing soak-growth checks passed. Peak RSS was
  349.18 MiB; first/last five-sample median RSS grew 21.23 MiB (budget 64 MiB).
  Median book backlog stayed 2 → 2. Median pool-checkout p95 changed
  9.12 → 14.51 ms, within the existing 20 ms floor. There was no observed HALT.
- Read-only session observations confirmed generations 0 through 5 and five
  completed handovers. Random selected `follow_market`, `sideways`, `sideways`,
  `follow_market`, `follow_market`, `sideways`. This random draw does not provide
  long-duration `follow_token` coverage; earlier scenario tests retain their
  own scope. The next scheduled handover fell near the measurement boundary; another
  fully observed step is not counted here.

## What the traces establish

**The timeout cluster occurs before the 50-minute handover.** Failed requests
started around UTC 03:14:35 and timed out around 03:14:40–41; the next step's
scheduled boundary was approximately 03:15:27.93. Temporal proximity to the
handover is not proof that switching profiles caused these timeouts.

Within the cluster, correlated pool checkout reached **2,816 ms**, a server
handler reached **6,200 ms**, and scheduled client latency reached **6,667 ms**.
The slowest queued success spent **5,484 ms in the client worker queue** before
network dispatch. Separate failed-request excerpts preserve request IDs and
server spans even when queued successes rank higher by total latency.

A watchdog caught the main event-loop thread **2,500 ms** behind its heartbeat
inside `StrategyV2Base.update_executors_info`. Other overlapping samples caught
response serialization and WebSocket processing. Bot cgroup counters over a
roughly 28.8-second surrounding interval recorded 18.77 CPU-seconds and 3.29
seconds of throttling. These are evidence of a combined pool/scheduling/event-loop
stall, not an exclusive CPU measurement for one function. Captured PostgreSQL
samples contained active backends and `ClientRead`, with no matching lock or I/O
wait observed. Sampling gaps mean a DB wait cannot be ruled out.

**A separate recurring cost is daily-statistics cache misses.** The existing
cache has a five-second TTL. Its aggregate scans historical fills and expands
fee JSON. The same query fingerprint took **1,106 ms** in the cold smoke,
**1,807 ms** in mixed and up to **1,685 ms** in soak. Soak daily-statistics p99
was **1,607 ms**. Other endpoints also exceeded one second at p99, so optimizing
this query alone has not been demonstrated to resolve the general stall.

**Synchronous persistence also appears in lag samples.** In mixed, a watchdog
caught the main thread inside ownership/commit code while heartbeat lag reached
1,352 ms. The largest individual measured mixed commit was 264 ms; the heartbeat
lag must not be attributed to that one commit's duration. Soak also captured a
commit-stack sample with 1,671 ms lag. Recorder wall time reached 759 ms in mixed
and 690 ms in soak. Changing persistence ordering requires a separate correctness
regression test; these observations do not justify weakening fencing.

No page-size-dependent N+1 appeared in the enforced checks: order pages at
limits 1/100/500 stayed at at most **3 SQL statements**, fill pages at most **1**,
and cached performance at most **1**. The measured problems are latency tails,
not evidence that adding a new cache backend will fix them.

## Instrumentation, overhead and evidence limits

Tracing is opt-in in the test harness. Request IDs connect scheduled client
timing to server SQL/pool/commit/recorder spans and existing observation tasks.
A watchdog records loop stacks, GC and cgroup counters. One persistent client-side
PostgreSQL connection samples waits/blocking PIDs every 250 ms; additional
minute-scoped read-only queries observed session generations. SQL parameters,
request bodies, credentials and frame locals are excluded; SQL literals are
redacted. No production endpoint, tracing framework or service was added.

The soak retained **13,987 in-window PostgreSQL samples**, **217 watchdog stall
samples**, and 23.49 MB of server / 9.13 MB of client JSONL. No stream records
were reported dropped by the recorded counters. The sampler hit its diagnostic
200 ms statement timeout **8 times** (`QueryCanceled`); these sampling gaps are
separate from the ten client timeouts. Mixed had one sampler timeout per window.

Each retained root is capped at 128 child spans. In the soak, **2,214 child spans
were omitted from `service.observe_once` roots across the whole file**, including
2,112 in roots overlapping the measurement window. HTTP request roots did not
lose child spans. Root durations, watchdogs and loss counters remain available;
the trace is not a complete SQL census during those large service roots. Server
counters are five-second runtime snapshots; forced disposable-container teardown
does not produce a final graceful-shutdown summary. JSONL flushes every second.

Recorded soak trace-writer CPU was approximately 2.04 s server-side and 2.17 s
client-side; the sampler thread used 10.74 CPU-seconds. These exclude hot-path span
construction and other instrumentation overhead. There was no paired one-hour
traced/untraced comparison, so the overhead and the runtime's intrinsic share
of the stall are not isolated. Thread CPU is inclusive, and async spans include
interleaved work; nested durations must not be added.

The earlier image was absent from Docker. This run rebuilt commit
`ed98eaf18eeba375a598ffb96befee7c3f0cc895`; nineteen selected runtime/SQL source
hashes match the prior accepted artifact. The new image is
`sha256:868cc6642d2aa1c272c69a5dc8707509a78eee676c1f18ef0218efcb6c621a35`
(size 1,364,516,628 bytes), pinned to registry manifest
`sha256:fa6e2af858df24af51c5236a80aeb67174eae6e8d523ba4931d41f7ac1e87cfc`.
Docker resources and PostgreSQL 14.15/image match the earlier probe. The host is
ARM with amd64 bot/client emulation. Source matching does not establish binary
identity or the exact cause of the historical untraced p99 spike. These are
local simulator measurements; historical Demo/E2E evidence retains its old scope.

## Verification and next gate

Sixteen focused tests pass, including Red/Green coverage for keeping failed
requests outside the slowest-request cutoff. Coverage also checks SQL redaction,
worker span parentage, context lifecycle, file/queue/span bounds, window append,
and PID/time correlation. Raw Red/Green logs are under `build/load-tracing/`.
Post-processing was extended after the runs; the portable artifact records its
current source hash separately from each run's original harness/fixture hashes.

Reproduce the timeout cluster with controlled traced/untraced runs, instrument
exclusive synchronous report-building/serialization work and separate pool
queueing from pre-ping/connection acquisition. Any confirmed runtime fix needs
Red/Green tests and another unchanged sixty-minute **zero-error** gate. That item
remains open in the [implementation plan](../../docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).

Raw run directories, immutable identities, script/fixture/trace SHA-256 values,
per-route metrics and failure excerpts are indexed in the portable JSON.
Reproduction commands and trace limits are documented in the
[load guide](../../docs/life-mm-load-tests.md#opt-in-causal-diagnostics).
