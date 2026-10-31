# Release measurement follow-up

Status: bounded source/microbenchmark checks and packaged pair pass; full
R1/R2 validation remains pending. Production image5973410d is unchanged.
The failed [prior release result](mm-release-closeout.md) remains failed.

## Confirmed observer cost and correction

Retained engine samples include255 stacks inside `probe -> distribution`; API
samples include49. Two signal stack dumps independently show the engine's main
thread inside quantile calculation. These are observations of a contributing
observer cost, not proof of the entire6.476s probe episode or historical causes.

The original exporter sorts each bounded sample series on the measured event
loop every5s. On the same amd64 image,65 full12,000-value series cost208–302ms
per export, compared with11–14ms at1,000 values. A controlled heartbeat comparison
observes a maximum285.26ms gap with inline exports and42.65ms after offloading;
maximum snapshot submission33.99ms. This is a five-export diagnostic with fixed
synthetic numeric series, not a service load or universal latency bound.

The corrected test helper copies scalar samples, then computes exact quantiles,
reads Linux process counters and atomically writes metrics on a dedicated single
worker. At most one job is in flight; busy submissions are counted/skipped,
never queued. Completed job failures surface to subsequent submission/shutdown.
The loop keeps its original50ms heartbeat and5s sampling policy. No probe timeout,
supervisor grace, runtime strategy or PostgreSQL policy changes.

Red/Green regressions cover a blocked export while loop callbacks still execute,
no overlap/backlog, immutable sample copies, surfaced output failure, atomic
publication and exact quantiles. The initial selected suite passes88 cases;
the final expanded related suite passes114 after scope-guard regressions.

## Memory measurement contract, declared before the next gate

A standalone controlled fixture writes two capped32MiB trace streams through the
existing sink. Measured cgroup current rises18.13MB to51.42MB, file cache rises
0.029MB to32.94MB, while process RSS rises28.06MB to28.64MB. After the experiment's
explicit sync/cache advice, current falls18.38MB with RSS28.77MB. Advice is used
only in this isolated causal experiment; no cache eviction or forced GC is
introduced into service/load acceptance.

The experiment establishes reclaimable trace-cache cost in this fixture. It does
not retroactively attribute the prior run's entire70.68MiB growth to trace.
New exports measure `memory.current`, `memory.peak`, `memory.stat.file` and
`memory.stat.shmem` from the beginning. The new retention value is:

```
retained_bytes = memory.current - (file - shmem)
```

This excludes reclaimable disk cache while keeping anonymous memory, kernel
charges and tmpfs/shared memory. The unchanged64MiB growth budget compares the
first/last five minute medians of this value. Each process still has its own
64MiB RSS growth limit plus thread/FD/PID limits. The original1.5GiB absolute
container-peak limit still includes **all** cache and memory. Raw cgroup growth
is always published alongside retention; neither a shrinking cache nor stable
process identity may hide process RSS growth or a restart.

Partial, inconsistent, negative or boolean breakdowns cannot pass. Historical
samples with no breakdown keep their original conservative current-memory rule;
there is no estimated cache subtraction or reclassification of old failures.
Regressions explicitly reject65MiB anonymous growth and80MiB tmpfs growth while
accepting80MiB measured reclaimable file growth within the separate absolute cap.

## Query replay

A disposable PostgreSQL14 container with2CPUs/512MiB and100,000 original fixture
fills executes `daily()` in516.85ms. Captured EXPLAIN ANALYZE query execution:
fill aggregate267.01ms, original-currency fee aggregate166.21ms. JIT remains off
inside the same existing transaction; no deadline, cache, pool or query is changed.
Fill totals retain all100,000 rows. This isolated run does not reproduce the
prior `QueryCanceled`, so no speculative reporting rewrite is made.

## Validation scope

A new bounded ordinary-entrypoint probe mode explicitly cannot qualify as R2;
normal R2 still requires its passing full baseline and warm300/mixed900.
The packaged pair uses the same image/effective YAML/client/exchange/history/
CPU/pool/cache policies, with only the declared observer differences. Required
fixture hashes match. Each300s run reconciles300 unique fills/original fees and
venue positions with zero unexpected errors:

| Entrypoint | p95 | p99 | Result |
| --- | --- | --- | --- |
| Corrected recorder | 62.64ms | 610.26ms | Pass, diagnostic only |
| Shipping service | 51.16ms | 115.08ms | Pass, diagnostic only |

Recorded run `20261101-010152-41620f` keeps the same supervisor/engine/API PIDs
and has zero skipped exports in retained minute samples. Shipping control
`production-control-20261101-010823-67cea7` explicitly has
`baseline_acceptance_eligible=false`; a passed short/diagnostic baseline cannot
qualify as R2. These separate short runs do not establish a full-hour improvement
or close R1/R2. The recorder still costs CPU/GIL time; offloading is not universal
isolation. Persistent stopped Demo image/control/ledger fingerprints are unchanged.

The old full result still evaluates as `legacy_cgroup_current` and fails
`rss_growth`; the corrected measurement does not relabel it. The next full gate
uses the declared new measurement on fresh samples, with all other original
budgets and six recovery cases retained. A fresh passing R1 is required for R2.

Portable [evidence](mm-measurement-remediation-evidence.json) retains both runs,
exact microbenchmarks, memory contract and historical-result preservation.

Raw evidence: ignored `build/release-diagnostics-20261101/` contains the native
Red/Green logs, exact microbenchmark outputs, query plans and cache experiment.
No connected exchange trading or maintenance of the persistent Demo is performed.
