# Cleanup state image: full load and image cleanup

Final image `f74d2a2a…` passes the full local/offline baseline, including the
unchanged sixty-minute soak and all five fault windows. The runner exits zero with
`passed=true` and `release_scope_complete=true`. Three unused bot images are removed
after verification. The existing Demo service/database is unchanged.

## Candidate and workload

- Runtime source: `2f6cbd257`; tag `hummingbot-cloudrun:cleanup-state`.
- Image/config: `sha256:f74d2a2abcad64dfdd9ad45c3491823c53a30f930da492b9c7c3ae3cb96526c8`.
- Manifest: `sha256:393cfc9f06d0d0c9942a32f56d6525f8d3662cadcc43dbf1a78740723783932f`.
- Test observer source: `a22e08d1e`; no runtime/image rebuild for that test-only fix.
- Raw evidence: `build/mm-load/20261106-203947-19b9f0/result.json`;
  log: `/tmp/hbot-cleanup-state-release-load-final.log`.
- Portable [evidence](admin-cleanup-state-load-evidence.json) retains result hashes,
  workload markers, failures, resource samples, probe summaries and image inventories.

The `all --security --operations --incident-diagnostics --cpus 2
--daily-cache-ttl-seconds 15` profile uses the established baseline: 20 main reads/s,
five auxiliary reads/s, 100 book updates/s, one synthetic fill/s and the existing
500-update/s burst stimulus. Independent exchange counters measure book production;
ledger reads separately verify settlement. There are 100,000 historical orders,
100,000 fills and 100,000 audit rows, seeded during a verified offline preparation.

Signed offline Google identities use fixture certificates; Basic is disabled and
managed bundles use local encryption without environment-key fallback. The test-only
provider outage is restored before intentional recovery faults. Main latency limits
remain p95 500 ms / p99 1,000 ms, with zero unexpected healthy-window errors,
including stale reporting responses. No rate, tolerance, GC or resource budget changes.

The app has two CPUs / 2 GiB, separate API/engine processes and bounded DB pools;
PostgreSQL has two CPUs / 512 MiB. The amd64 app runs emulated on an arm64 Mac.
Full tracing, MM activity tracing and enhancement flags are disabled in this
baseline; bounded incident capture is enabled. The earlier six native-image cleanup
recovery cases in the [state review](../performance/admin-cleanup-state-review.md)
remain separate evidence, not scenarios rerun or inherited into this full gate.

## Healthy load results

| Window | Seconds | Main reads | p95 / p99 | Command admission p95 | Unexpected errors |
| --- | ---: | ---: | --- | ---: | ---: |
| Warmup | 300 | 6,000 | 75.69 / 419.92 ms | 30.32 ms | 0 |
| Mixed | 900 | 18,000 | 62.97 / 309.11 ms | 32.45 ms | 0 |
| Bursts | 90 | 1,800 | 66.15 / 152.63 ms | 109.79 ms | 0 |
| Random soak | 3,600 | 72,000 | 67.55 / 353.91 ms | 38.05 ms | 0 |

All **97,800 main reads** and **24,450 auxiliary reads** return HTTP 200. Soak
auxiliary p99 is 152.53 ms. Mixed sustains 100.003 book updates/s and one fill/s;
the existing burst assertion passes. Soak observes 99.966 book updates/s and
3,594 synthetic fills, including the existing intentional random-transition gaps.
The unchanged scheduler, fill shortfall policy and budget checks all pass.

At the end of soak, all **4,884 independent exchange/recorder fills**, exact fees
and positions reconcile; two identified funding bills and three daily snapshots
are checked. These are simulated fills, not trades on an OKX Demo account.
Security traffic adds 1,467 requests, 163 role cycles and 489 expected denials.
Audit pagination remains one query/page; credential status remains three queries.

## Retention and availability

The soak retains **58 samples**, with a maximum sample gap of **65.50 seconds**
against 90 seconds. Every sampled runtime is running. Supervisor/API/engine PIDs
remain 1/32/31; healthy-window logs contain no ERROR or child lifecycle events.
Engine and API FD counts end where they start (26 and 23); thread counts remain
14 and 16. Short-lived FD peaks are retained in the evidence.

The unchanged retained-memory basis is `cgroup_current_minus_file_plus_shmem`.
Early/late medians are 660,221,952 / 686,309,376 bytes: growth **24.88 MiB**, below
**64 MiB**. Per-process RSS is recorded separately. This finite test does not prove
zero memory growth over an unlimited runtime.

Bounded incident analysis finds no probe episode in warmup/mixed/bursts and **six
engine episodes during soak**, all recovered inside the window; longest observed
transition **1.239 seconds**. These supervisor observations are not measured quote
downtime. Capture has bounded retention/span loss and MM activity tracing is off;
the result does not establish an exclusive cause or continuous two-sided quoting.

All five separate fault windows pass: stale feed, owner replacement, queued expiry,
DB lock delay and DB outage. Owner replacement and DB faults intentionally produce
503s and may restart/fence the engine; they are excluded from healthy latency.
DB-fault recovery preserves HALT until the existing test-only reviewed recovery
step. This is not a claim that real dependency outages never interrupt trading.

## Failed attempts and the observer correction

Both earlier attempts remain failures in the portable evidence:

| Attempt | Finding |
| --- | --- |
| `20261106-184747-480917` | Host clamshell sleep from 18:51:25 to 20:18:48 +07 invalidates continuous acceptance: sample gap 5,324.8 s. A shutdown 51603, engine replacement and HALT are retained; sleep does not exclusively explain each event. |
| `20261106-202647-95cca9` | Warmup fails only the original market-rate tolerance: 30,004 updates over 300.127587637 s = 99.9708165/s. API p99 347.14 ms, zero unexpected errors and 300 exact fills do not turn that failed window into a pass. |

The normal-window book marker previously came from financial `/ledger` before
initial workload preparation. A controlled test shows that preparation/serialization
delay can be charged to the producer. Commit `a22e08d1e` brackets the actual workload
with cheap independent `/market-metrics` markers while retaining `/ledger` settlement
checks. Two Red cases fail before the change; 50 related tests pass afterward.
An actually slow 80-update/s producer still fails. Counts, scheduling, tolerance
and runtime image stay unchanged. This corrects a measurement weakness, not a
confirmed production defect or an exclusive explanation of the historical failure.

## Image cleanup

After the full runner exits, a fresh inventory checks both running and stopped
container image references. Exact unused bot IDs removed without force:

- `6d9ddbf99ddd…`: intermediate snapshot projection image.
- `8cdadb21e6ff…`: superseded cleanup polling image.
- `89d5e7c61fcf…`: superseded cleanup recovery image.

Unique image count falls **17 → 14**. Candidate `f74d2a2a…`, known full-gate rollback
`1bb946c3…`, all container-referenced images and other projects remain. All **10
containers** and **14 volumes** match the before/after inventory, including unchanged
Demo start timestamps. No container, volume or build-cache pruning is performed.
Docker logical image sizes share layers; no physical reclaimed-byte claim is made.

Real Google sign-in, real GCP KMS/IAM and reviewed existing-Demo managed-credential
cutover still require external acceptance. This local result does not close them.
