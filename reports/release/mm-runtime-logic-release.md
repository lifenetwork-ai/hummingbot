# Runtime logic fixes: final-image load rerun

Date: 2026-11-02 (Asia/Saigon). **Full unchanged R1 and matched shipping R2 pass**
on image `95cde31c` / manifest `24ea415a`, source `7b727628f`.
[Portable evidence](mm-runtime-logic-release-evidence.json).
[Fixes and source regression checks](../investigations/mm-runtime-logic-audit.md).

The image contains durable-HALT cleanup and exact Decimal fee-replay fixes.
No application, configuration, workload, duration or acceptance-budget change
was made during this rerun. All packaged source/config hashes and the frozen
measurement source are independently checked after the gates. Existing source
verification (2,310 native tests plus 11 subtests, 296 PostgreSQL tests, Swagger,
two targeted E2E groups and external HALT/restart) belongs to the preceding
audit on this unchanged image; those suites were not rerun in this load task.

## Measured results

| Gate/window | Duration | API p95 | API p99 | Unexpected API errors |
| --- | ---: | ---: | ---: | ---: |
| R1 warmup | 300s | 50.12ms | 99.26ms | 0 |
| R1 mixed | 900s | 57.91ms | 534.24ms | 0 |
| R1 burst | 90s | 55.73ms | 95.38ms | 0 |
| R1 Random soak | 3,600s | 61.88ms | 465.40ms | 0 |
| R2 ordinary warmup | 300s | 51.08ms | 65.03ms | 0 |
| R2 ordinary mixed | 900s | 52.68ms | 65.30ms | 0 |

All original healthy latency/rate budgets pass. R1 independently reconciles
4,885 fills, original fees, positions and two funding bills. Random has actual
running coverage of all three profiles. R2 independently reconciles 1,200 fills,
fees/positions and two bills in a fresh DB. R2 uses the ordinary production
entrypoint with matching effective fixtures, 2 CPU, 2GiB container limit and
15s daily-cache TTL. Its scope is warmup/mixed, **not a second hour soak**. The
control's raw diagnostic=true/release_scope_complete=false metadata is retained;
its separate matched-baseline acceptance checks pass.

Each disposable DB contains the unchanged synthetic history of 100,000 orders
and 100,000 fills. R1 keeps the all-feature/volatility-defense/operator-read
fixture with bounded incident/GC/market diagnostics; R2 preserves the matching
business fixture and client load while using the unwrapped shipping entrypoint.

The soak retains 58 runtime samples, maximum gap 66.15s, with stable sampled
supervisor/engine/API PIDs and no unplanned child replacement or HALT in the
healthy envelope. Retention grows **12.69MiB**, below the unchanged 64MiB budget;
the basis is cgroup current minus file cache plus shmem. Raw cgroup growth is
35.95MiB. Peak container memory is 746.65MiB, below 1.5GiB. Early/late engine/API
RSS medians grow 7.50/5.63MiB, thread medians stay fixed and FD medians grow 1/0.
This validates the measured one-hour contract, not indefinite leak freedom.

All six separately classified injected fault groups pass: feed, volatility
defense, owner restart, queued expiry, database delay and database failure.
Intentional disruptions remain excluded from healthy latency; DB uncertainty
retains HALT instead of silently restoring trade permission.

## Residual probes and attribution

Passing the gates does not mean zero stalls. R1's healthy envelope has **38 engine
and two API probe failures/recoveries**, all recovered, with the longest reported
interval 2.866s. By failure window: warmup has one engine episode, mixed six
engine/two API, burst two engine and soak 29 engine. R2 has two recovered engine
episodes, longest reported interval 1.308s. No healthy-envelope child spawn,
replacement or shutdown marker is observed. These intervals measure supervisor
observations, not quote downtime.

Bounded incident review finds overlapping observer/recorder/ownership/GC work;
overlap does not prove a unique cause. Prehistory evictions and filtered spans
are retained in evidence. R1 instrumentation and R2's different duration prevent
a direct causal comparison of episode counts. Historical probe/p99 attribution
remains open; this task does not claim to resolve it or establish a performance
improvement from the two logic fixes.

One premeasurement launch received a Docker image-inspect lookup failure; the
next inventory and identical launch found the original image. No measured window
started in that attempt, no rebuild occurred and its cause is unproven. The
subsequent complete R1/R2 sequence ran without measured retries or stitched
windows. The failed launcher log is retained separately.

## Artifacts and cleanup

- R1: `build/mm-load/20261102-171755-763004`.
- R2: `build/service-supervision/production-control-20261102-184714-75d846`.
- Runner, independent verifiers, incident analysis and source freeze:
  `build/logic-audit-load-20261102/`.

Owned containers/networks and the temporary registry tag are removed; image
`hummingbot-cloudrun:logic-audit` and prior images remain. Original Demo
volume/account/config and unrelated containers are untouched. No connected Demo
session, service upgrade, real trading or Cloud Run deployment is included.
