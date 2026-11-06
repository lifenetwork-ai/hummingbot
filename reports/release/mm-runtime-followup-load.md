# Runtime follow-up image: full local load gate

Image **`bf4e1f77…` passes** the unchanged full offline load gate, sixty-minute
Random soak and all five injected faults. Four targeted image E2E groups and a
separate enabled-enhancement diagnostic also pass. One unused bot image is removed;
all existing container/image/start-time references and volumes are preserved.

## Candidate and scope

- Tag: `hummingbot-cloudrun:runtime-followup`; amd64; 1,374,446,698 bytes (~1.37 GB).
- Config/image: `sha256:bf4e1f7798fd6ecea05bd96a63f789a39a21409e51ce9a5cc1ac9843056ab745`.
- Manifest: `sha256:71d6bb3e277ae5d175940cac92054fb237bfc083ec3be0a21c57fb357473a6ac`.
- Build commit `b6b2f7d1c`; runtime follow-up `45707b06e`, `1f4f19de6`, `504343b9b`,
  and recorder change `c9889da28`. All **1,224 packaged source/config hashes** match
  and remain unchanged through verification.
- Raw full result: `build/mm-load/20261107-022057-f11e53/result.json`.
- Preparation/E2E/probe/cleanup: `build/runtime-followup-release-20261107/`.
- [Portable evidence](mm-runtime-followup-load-evidence.json) retains raw hashes,
  counters, scoped incidents, resources, source identities and cleanup inventory.

The same `all --security --operations --incident-diagnostics --cpus 2
--daily-cache-ttl-seconds 15` baseline uses 20 main reads/s, five auxiliary reads/s,
100 book updates/s, one synthetic fill/s and the existing 500-update/s burst.
History includes 100,000 orders, 100,000 fills and 100,000 audit events, seeded
while the service is stopped. The bot has two CPUs/2 GiB; PostgreSQL two CPUs/
512 MiB. The amd64 bot is emulated on an arm64 Mac. Latency, zero-error, retention,
query-count and resource budgets are unchanged. Full tracing and MM activity
diagnostics remain disabled; incident capture remains bounded.

## Healthy load

| Window | Seconds | Main reads | p95 / p99 (ms) | Unexpected errors |
| --- | ---: | ---: | --- | ---: |
| warmup | 300 | 6,000 | 106.39 / 371.55 | 0 |
| mixed | 900 | 18,000 | 112.31 / 634.60 | 0 |
| bursts | 90 | 1,800 | 74.23 / 319.44 | 0 |
| soak | 3,600 | 72,000 | 69.40 / 306.91 | 0 |

All **97,800 main** and **24,450 auxiliary** reads return HTTP 200. Healthy
logs contain no ERROR/CRITICAL record or child lifecycle event. Burst backlog
drains in **4.21 s** against the unchanged ten-second budget. At soak completion,
**4,885 independent simulated fills**, exact fees and positions reconcile;
identified funding bills and three daily balance snapshots are checked. These are
simulator fills, not OKX Demo trades. Role/audit traffic uses signed offline
Google fixtures, Basic disabled and managed local-encrypted credentials.

The soak contains **58 valid samples**, maximum gap **65.60 s** (exact value in
evidence), with all samples running and supervisor/engine/API PIDs unchanged
(`1/38/39`). Retained-memory growth is **21.89 MiB / 64 MiB**, using the unchanged
`cgroup_current_minus_file_plus_shmem` basis. Engine FDs move 26→30 and API FDs
21→23; per-role medians/ranges and unchanged threads are retained. The existing
bounded-resource gate passes; a finite hour does not prove unlimited leak-free
operation.

## Recovered probes and comparison limits

| Window | Engine episodes | Longest observed error→recovery interval |
| --- | ---: | ---: |
| warmup | 1 | 1.130 s |
| mixed | 1 | 1.364 s |
| bursts | 0 | — |
| soak | 1 | 1.047 s |

All three episodes recover inside their windows. No API episode is recorded.
These intervals describe supervisor observations, not measured quote downtime.
Bounded prehistory/span retention and disabled MM activity diagnostics prevent
a claim of uninterrupted quoting or a unique root cause.

Against the previous `f74d2a2a…` full run, soak p99 is 353.91→306.91 ms (~13%
lower), soak probes six→one and retained growth 24.88→21.89 MiB. Mixed p99 is
309.11→634.60 ms and burst p99 152.63→319.44 ms, both slower but within their
unchanged budgets. Host conditions are not fully controlled between runs;
per-component source benchmarks do not establish uniform full-system speedup.

## Additional scoped verification

Four shipping-image E2E groups pass: availability, drawdown, MM recovery including
DB restart, and initial-position restart. Each performs runtime ABI/JIT/TLS/SDK
and size smoke. These four groups are not the full E2E/upgrade matrix.

A separate ninety-second enabled-enhancement probe passes p99 **253.59 ms**, zero
unexpected errors and **90 exact simulated fills/fees/positions**. It exercises
drawdown/outflow/bootstrap/reporting paths, with local encryption/security and
operator reads; it is a diagnostic, not a substitute for the unchanged baseline.

All five injected fault windows pass: stale feed, owner restart, queued expiry,
DB catalog-lock delay and DB outage. Intentional owner/DB faults produce errors/
503s and may restart/fence the engine; these are explicitly excluded from healthy
latency. DB faults retain HALT until the existing simulator-only reviewed recovery
step. No fault recovery modifies the existing Demo or grants real trading authority.

## Image cleanup

After all runners exit, exact unused image `1bb946c39830…` and its local aliases
are removed without force. Unique images **15→14**; candidate `bf4e1f77…`, previous
full-gate rollback `f74d2a2a…`, all container references and other projects remain.
All **10 containers** and **14 volumes** match before/after cleanup; existing
Demo IDs/images/start timestamps also match the pre-test inventory. No test targets
the existing Demo database. No container, volume or build-cache pruning occurs;
logical image sizes share layers, so physical reclaimed bytes are not inferred.

The first cleanup helper invocation stopped before mutation because it looked
for the result in the wrong directory; correcting its artifact path allowed the
guarded cleanup to complete. This is a local helper error, not a runtime defect.

No real Google sign-in, GCP KMS/IAM, reviewed managed-Demo cutover or production
deployment is included. Source Red/Green/native/PG results remain in the
[remediation report](../implementation/MM_RUNTIME_FOLLOWUP.md).
