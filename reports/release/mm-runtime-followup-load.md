# Current image: full load after quantile export correction

Image **`a6fe88c0…` passes** all four healthy windows, the sixty-minute Random
soak and all five fault checks on the first attempt with the revised fixture.
**Three healthy engine probes still recover after about 1.05–1.18 s.** The
historical cluster's unique cause remains unproven; this is scoped acceptance,
not a claim of continuous quotes or uniformly lower latency on every host.

## Candidate and scope

- Same amd64 image `sha256:a6fe88c0bba185d27ed68d1fbc1f43dfa39c5a560c80df1f28e720f2afeb16b2`,
  build `745a86e3c`, 1,374,449,982 bytes; tag `hummingbot-cloudrun:runtime-copy`.
- Same cached manifest
  `sha256:7e615f650e8c7a6c5265bef45f685e74aae7b2096ebdf5d895916a8deb20e6aa`.
- Test-fixture commit **`41d290393`** changes quantile calculation in
  `load_process_metrics.py`; the preceding incident-lock fix remains. All
  **1,224 packaged runtime/config hashes** match before and after measurement.
  No new runtime image, production code, pool, I/O/probe or latency-budget change.
- Workload: `all --operations --security --incident-diagnostics --cpus 2
  --daily-cache-ttl-seconds 15 --keep-going`. Continue-after-budget-failure
  collects remaining phases and would still exit nonzero on a violation.
  This attempt has no healthy budget violation. Full trace, accelerated refresh,
  extra stage timers and MM activity instrumentation remain disabled.
- 100k orders/100k fills/100k audit events; 20 main and 5 auxiliary reads/s,
  100 book updates/s, one synthetic fill/s, 500-update/s burst. Bot 2 CPUs/2 GiB;
  PostgreSQL 2 CPUs/512 MiB; simulator/client each 1 CPU/512 MiB.
- Host: arm64 Mac emulating amd64. **AC power** here differs from the preceding
  failed battery-powered run. Inter-run latency differences cannot be assigned
  exclusively to the code correction.
- Raw result `build/mm-load/20261107-230357-1eb5f7/result.json`;
  runner/analysis `build/quantile-export-load-20261107/`.
  [Portable evidence](mm-runtime-followup-load-evidence.json) retains hashes,
  measurements, omissions, fault errors and the preceding failed attempt.

## What was corrected

The test metrics worker sorted every retained sample ring to retrieve four
quantiles, consuming interpreter CPU/GIL time alongside the measured loop.
Large numeric rings now use existing NumPy to select the same exact nearest
ranks; short rings retain sorting. Worker count, one-job admission, five-second
cadence, sample capacities, output schema and percentile definitions stay fixed.

On explicit 54-ring/380,006-value fixtures, seven alternating image pairs show
median quantile CPU **77.10→39.88 ms/export**. Small-ring cost rises about
0.054 ms/export. Component heartbeat improvement and a native outlier are both
retained. These are component measurements, not a causal explanation for every
historical HTTP/probe delay. Red 1 fail / 12 pass; 80 targeted tests pass before load,
81 after an additional long-ring integer-precision case. [Source/TDD details](../implementation/MM_RUNTIME_FOLLOWUP.md#quantile-export-work--2026-11-07).

## Healthy load

| Window | Seconds | Main reads | Main p95 / p99 (ms) | Unexpected errors | New simulated fills |
| --- | ---: | ---: | ---: | ---: | ---: |
| warmup | 300 | 6,000 | 56.41 / 312.31 | 0 | 300 |
| mixed | 900 | 18,000 | 59.76 / 366.46 | 0 | 900 |
| burst | 90 | 1,800 | 64.57 / 168.04 | 0 | 90 |
| Random soak | 3,600 | 72,000 | 65.75 / 287.63 | 0 | 3,594 |

All **97,800 main / 24,450 auxiliary** requests return HTTP200 with zero
unexpected stale responses or budget violations. Fixed mixed/burst retain
their rate checks; Random admission gaps retain the original observed-rate
policy. Burst backlog drains in **2.01 s / 10 s**. Query-count/page-size and
exchange-account-read limits pass unchanged.

Final **4,884 unique simulated fills**, exact quantities/fees and held/active
positions reconcile, together with two identified funding bills and six daily
snapshots. These are simulator records, not OKX Demo trades.

All **58 soak samples** record running with supervisor/engine/API PIDs
**1/32/33**; maximum gap **65.51 s**. Retained growth is **23.16 MiB / 64 MiB**
on `cgroup_current_minus_file_plus_shmem`; raw cgroup growth is 24.96 MiB.
Engine FDs 26→27 (range 26–33), API 20→22; threads stay 14/16. This finite check
does not establish unlimited leak-free operation.

## Recovered probes and comparison

| Window | Previous failed attempt p99 (ms) | Current p99 (ms) | Previous → current engine probes |
| --- | ---: | ---: | ---: |
| warmup | 394.79 | 312.31 | 1 → 0 |
| mixed | 1,315.91 | 366.46 | 6 → 1 |
| burst | not run | 168.04 | not run → 0 |
| soak | not run | 287.63 | not run → 2 |

The mixed probe recovers in **1.081 s**, soak probes in **1.053 / 1.177 s**.
Retained markers contain no extra healthy/inter-window failure. These intervals
are supervisor observations, not quote downtime. No MM activity tracing is
present in this baseline, so healthy PIDs and reconciled fills cannot establish
continuous quotes for every moment of each interval.

Healthy logs contain **zero ERROR/CRITICAL and zero child lifecycle events**.
Before measurement there are six seed/preparation child starts and zero errors;
between/after healthy phases there are zero errors or child lifecycle events.
At soak end, both incident sinks have zero dropped records, zero suppressed
incidents and zero skipped metric exports. Child-span omissions remain explicit:
engine 61,793 / API 7,596. Bounded history is not a complete causal timeline.

The previous failed run remains in Git **`41d290393`** at the current report and
evidence paths; its 2.6-second slow minute and eight premeasurement blocked-order
logs are retained there and in the linked prior evidence. The earlier accepted
same-image full gate remains in Git `dfad4a4d1`; it had soak p99 406.14 ms and
eleven recovered soak engine probes. None of those older outcomes is erased or
retroactively passed by this run.

## Fault checks and environment

All five pass: feed loss, owner restart, queued expiry, database delay and
database outage. Raw fault `unexpected_errors` counters are **0 / 293 / 0 /
322 / 365**, respectively; expected HTTP503/transport failures remain visible
and excluded from healthy latency acceptance. Eight ERROR log records and two
scheduled child restarts occur inside injected fault windows, including the
two deliberate DB ownership/session failures. Fault fixtures reconcile/recover
explicitly; they do not silently bypass an ownership HALT.

One injected engine probe recovers in 1.090 s; another has no retained recovery
marker. Keep that missing context separate and never pair across replacement
to invent continuous downtime. All-phase log classification is in the evidence.

All **10 existing containers/start states and 14 volumes** are preserved,
including the running Demo service/database. Harness containers are removed;
no image was built or removed. Candidate `a6fe88c0…`, rollback `e00cef38…` and
all earlier image IDs remain. Original same-image E2E/enhancement90/activity300
entries retain their earlier dates/fixtures; those groups are not rerun here.
No real OKX, Google/GCP or production deployment occurs.

The full workload acceptance is closed for this fixture/image. Exclusive
historical-cause attribution and the production continuous-quote SLO remain
open in the [remaining-work register](../../docs/plans/README.md).


Source follow-up 2026-11-08: the subsequent book/debug/report allocation changes
have native and component verification only. They are not packaged in this image
and inherit none of this load acceptance. See the
[source report](../implementation/MM_RUNTIME_FOLLOWUP.md) and pending new-image
item in the remaining-work register.
