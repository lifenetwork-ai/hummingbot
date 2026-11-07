# Ownership-read image: full local load gate

Image **`e00cef38…` passes** the unchanged full offline load gate, sixty-minute
Random soak and all five fault windows. Four targeted E2E groups pass on their
first attempts. Recovered probes remain: five engine episodes in soak, one API
episode in mixed, plus one engine episode between warmup and mixed. One
superseded unused image is removed.

## Candidate and scope

- Tag `hummingbot-cloudrun:owner-read`; amd64; **1,374,449,073 bytes** (~1.37 GB).
- Image `sha256:e00cef387eec20aed77ca5411caadf8eaf65c08e527492037fd0392fb7e8d5ca`.
- Manifest `sha256:c24f760350bac7cf86af0ba05fc77c44e98fe6c438b5741a1f5600dd7e377327`.
- Build commit `ff6b85ded`; ownership-read runtime `d9488143d`, including recorder
  optimization `e030cfcdc`. All **1,224 packaged source/config hashes** match and
  remain unchanged through measurement. No probe, I/O, retry, pool, CPU or load
  budget changes.
- Full result `build/mm-load/20261107-130723-e97f9b/result.json`;
  preparation/E2E/cleanup `build/owner-read-release-20261107/`.
- [Portable evidence](mm-runtime-followup-load-evidence.json) retains results,
  source/fixture/log hashes, incidents, role resources, preceding gate comparison
  and guarded image cleanup. The full runner exits successfully; no attempt on
  this image needs a retry.

The unchanged `all --security --operations --incident-diagnostics --cpus 2
--daily-cache-ttl-seconds 15` baseline uses 20 main reads/s, five auxiliary reads/s,
100 book updates/s, one synthetic fill/s and the 500-update/s burst. It seeds
100,000 orders, 100,000 fills and 100,000 audit events while the service is stopped.
Bot limits are two CPUs/2 GiB, PostgreSQL two CPUs/512 MiB; amd64 is emulated on
an arm64 Mac. Full tracing and MM activity diagnostics remain disabled; incident
capture is bounded. Real OKX/Demo, Google/GCP and deployment are outside this run.

## Healthy load

| Window | Seconds | Main reads | p95 / p99 (ms) | Unexpected errors |
| --- | ---: | ---: | --- | ---: |
| warmup | 300 | 6,000 | 60.64 / 327.65 | 0 |
| mixed | 900 | 18,000 | 68.41 / 387.34 | 0 |
| bursts | 90 | 1,800 | 70.30 / 267.47 | 0 |
| soak | 3,600 | 72,000 | 83.72 / 410.20 | 0 |

All **97,800 main** and **24,450 auxiliary** reads return HTTP200; no unexpected
stale response or declared budget violation. Healthy parsed logs contain no
ERROR/CRITICAL or child lifecycle event. Burst backlog drains in **0.95 s** against
the unchanged ten-second budget. **4,884 independent simulated fills**, exact fees
and positions reconcile; two identified funding bills and three daily snapshots
also match. These are simulator fills, not trades on an actual OKX Demo account.
Soak throughput is observed; intentional Random-step admission gaps retain the
existing policy, and fixed mixed enforces its original rate checks.

All **58 valid soak samples** are running; maximum gap **65.61 s**, role PIDs
**1/32/33** remain unchanged. Retained growth is **28.59 MiB / 64 MiB**, using the
unchanged `cgroup_current_minus_file_plus_shmem` basis. Engine FDs 25→31 (range
25–34), API 22→23; engine/API threads stay 14/16. The resource gate passes; a finite
hour does not prove unlimited leak-free operation or continuous two-sided quoting.

## Recovered probes and comparison

| Measured window | Role and episodes | Longest supervisor error→recovery interval |
| --- | --- | ---: |
| warmup | none | — |
| mixed | API: 1 | 1.310 s |
| bursts | none | — |
| soak | engine: 5 | 1.068 s |

All six recover within their windows. Soak intervals range **1.041–1.068 s**.
An additional engine timeout between warmup and mixed recovers in **1.056 s**;
it is outside both measured windows and is disclosed separately. Seven recovered
probe episodes are therefore retained across healthy measurement and its internal
transition. Fault-window and post-fault markers are separately classified in
`probe_marker_inventory`; a superseded failure is not paired across a later
failure/replacement to invent a recovery interval.

These are supervisor observations, not measured quote downtime. Bounded captures
and disabled MM activity diagnostics do not establish uninterrupted quoting or a
unique cause. The engine incident recorder reaches its 32-capture limit and
suppresses four further context captures; its sink reports zero dropped records.
Retained markers do not provide complete stack/span history.

Compared with preceding accepted image `837e7997…`:

| Measurement | Previous | Current |
| --- | ---: | ---: |
| Warmup p99 (ms) | 298.12 | 327.65 |
| Mixed p99 (ms) | 494.03 | 387.34 |
| Burst p99 (ms) | 362.80 | 267.47 |
| Soak p99 (ms) | 360.68 | 410.20 |
| Retained growth (MiB) | 24.05 | 28.59 |
| Soak engine probe episodes | 7 | 5 |
| Measured-window probe episodes | 8 | 6 |
| Extra probe between healthy windows | 0 | 1 |

Mixed/burst latency, burst drain and probe counts improve; warmup/soak latency and
retention regress, all within unchanged budgets. Host conditions are not fully
controlled, so these observations do not establish a causal uniform speedup.
The separate controlled ownership-read result (fresh SELECT retained, no separate
read COMMIT, paired batch means/read 2.209→1.210 ms) does not uniquely explain every
historical timeout. Preceding full evidence, including its first failed shutdown
poll and passing unchanged retry, remains in Git **`ff6b85ded`** under this report's
path and `reports/release/mm-runtime-followup-load-evidence.json`.

## E2E and fault scope

Availability, drawdown, MM recovery/DB restart and initial-position restart pass
on the same image, all on their first attempts. Each includes runtime ABI/JIT/TLS/
SDK and size smoke; these four groups are not the full E2E/upgrade matrix.
Initial-position restart retains two exact fills/fees/positions and a pinned
session; shutdowns measure **2.299/1.333 s**. The preceding image's unexplained 9.5s
shutdown poll timeout is historical evidence, not erased by this new pass.

A separate enabled-enhancement90 diagnostic passes: p99 **437.62 ms**, zero
unexpected errors, **90 exact simulated fills**, funding bills and daily snapshots.
It exercises drawdown/outflow/bootstrap/reporting paths with local encryption and
security/operations; it does not replace the baseline.

All five fault windows pass: feed, owner restart, queued expiry, DB catalog-lock
delay and DB outage. Their intentional errors/503s/restarts are separate from
healthy latency and retained in the evidence. The existing simulator-only reviewed
recovery preserves HALT before its reset; no fault changes the existing Demo
database or grants live authority.

## Cleanup and remaining scope

After successful gates and runner exit, image `bf4e1f77…` and its two local tags
are removed without force. Unique images **15→14**. New candidate `e00cef38…`,
accepted rollback `837e7997…`, every running/stopped container image and other
projects remain. All **10 containers / 14 volumes**, including Demo IDs/images/
start timestamps, match before and after. No volume/container/cache pruning;
shared logical image sizes do not establish physically reclaimed bytes.

No real Google/GCP, reviewed managed-Demo cutover, connected OKX test or production
deployment is included. [Source/TDD verification](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
retains the rejected variants, full native/PG verification and controlled
component measurements separately from this image gate.
