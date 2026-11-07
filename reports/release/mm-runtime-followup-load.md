# Fill-flush image: full local load gate

Image **`837e7997…` passes** the unchanged full offline load gate, sixty-minute
Random soak and all five fault windows. Four targeted E2E groups pass, with one
initial-position restart retry required; its first failure is retained below.
Brief recovered probes remain. One superseded unused image is removed.

## Candidate and scope

- Tag `hummingbot-cloudrun:fill-flush`; amd64; **1,374,446,975 bytes** (~1.37 GB).
- Image `sha256:837e7997478e1d477046763067cf15f2ea63d1c50df4b3442cd235fb2afca712`.
- Manifest `sha256:a6076901c6c3c2acfc2a66b9ee7b388d254ed5001710e9f41963cb7a2d903762`.
- Build commit `9ca14a148`; recorder optimization `e030cfcdc`.
  All **1,224 packaged source/config hashes** match and remain unchanged through
  measurement. No probe, I/O, retry, pool, CPU or load budget changes.
- Full result `build/mm-load/20261107-055822-faab3f/result.json`;
  preparation/E2E/retry/cleanup `build/fill-flush-release-20261107/`.
- [Portable evidence](mm-runtime-followup-load-evidence.json) retains results,
  source/fixture/log hashes, incidents, role resources, the failed E2E attempt,
  preceding gate comparison and guarded image cleanup.

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
| warmup | 300 | 6,000 | 60.04 / 298.12 | 0 |
| mixed | 900 | 18,000 | 71.75 / 494.03 | 0 |
| bursts | 90 | 1,800 | 79.87 / 362.80 | 0 |
| soak | 3,600 | 72,000 | 74.05 / 360.68 | 0 |

All **97,800 main** and **24,450 auxiliary** reads return HTTP200; no unexpected
stale response or declared budget violation. Healthy parsed logs contain no
ERROR/CRITICAL or child lifecycle event. Burst backlog drains in **1.50 s** against
the unchanged ten-second budget. **4,884 independent simulated fills**, exact fees
and positions reconcile; two identified funding bills and three daily snapshots
also match. These are simulator fills, not trades on an actual OKX Demo account.
Soak throughput is observed; intentional Random-step admission gaps retain the
existing policy, and fixed mixed enforces its original rate checks.

All **58 valid soak samples** are running; maximum gap **65.50 s**, role PIDs
**1/38/39** remain unchanged. Retained growth is **24.05 MiB / 64 MiB**, using the
unchanged `cgroup_current_minus_file_plus_shmem` basis. Engine FDs 26→30, API 19→23;
engine/API threads stay 14/16. The resource gate passes; a finite hour does not
prove unlimited leak-free operation or continuous two-sided quoting.

## Recovered probes and comparison

| Window | Engine episodes | Longest supervisor error→recovery interval |
| --- | ---: | ---: |
| warmup | 0 | — |
| mixed | 1 | 1.403 s |
| bursts | 0 | — |
| soak | 7 | 1.120 s |

All eight recover within their windows; no API episode is recorded. Soak intervals
range **1.012–1.120 s**. These are supervisor observations, not measured quote
downtime. Bounded captures and disabled MM activity diagnostics do not establish
uninterrupted quoting or a unique cause. The recorder optimization does not close
this attribution limitation.

Compared with preceding image `bf4e1f77…`, p99 changes are warmup 371.55→298.12 ms,
mixed 634.60→494.03 ms, burst 319.44→362.80 ms, soak 306.91→360.68 ms. Retained growth
is 21.89→24.05 MiB; total recovered probes 3→8 (soak 1→7). Some measurements improve
and others regress, all within unchanged budgets. Host conditions are not fully
controlled. The separate component result (3→2 guarded flushes, 13→12 statements,
p50 17.23→15.12 ms) does not establish uniform full-system speedup.
Preceding full evidence is preserved at Git `9ca14a148` under this report's path
and `reports/release/mm-runtime-followup-load-evidence.json`.

## E2E, retained failure and fault scope

Availability, drawdown, MM recovery/DB restart and initial-position restart pass
on the same image. Each includes runtime ABI/JIT/TLS/SDK and size smoke; these
four groups are not the full E2E/upgrade matrix.

The **first initial-position restart attempt fails** its 9.5s PID1 shutdown poll:
`build/e2e-offline/20261107-055110-93e6ca/result.json`. Logs contain clean engine
shutdown and supervisor exit-code 0 messages, but no retained container exit-state
timeline; its exclusive cause remains unproven. Preserve that failed result and
log hashes. An unchanged-image, unchanged-threshold retry passes at
`build/e2e-offline/20261107-055329-dad1e7/result.json`, shutdown 1.489/1.630s, exact
two fills/fees/positions and pinned session. No runtime/harness fix or threshold
increase is claimed; a pass does not erase the first failure.

A separate enabled-enhancement90 diagnostic passes: p99 **359.16 ms**, zero
unexpected errors, **90 exact simulated fills**, funding bills and daily snapshots.
It exercises drawdown/outflow/bootstrap/reporting paths with local encryption and
security/operations; it does not replace the baseline.

All five fault windows pass: feed, owner restart, queued expiry, DB catalog-lock
delay and DB outage. Intentional errors/503s/restarts are separate from healthy
latency. The existing simulator-only reviewed recovery preserves HALT before its
reset; no fault changes the existing Demo database or grants live authority.

## Cleanup and remaining scope

After successful gates and runner exit, image `f74d2a2a…` and its two local tags
are removed without force. Unique images **15→14**. New candidate `837e7997…`,
accepted rollback `bf4e1f77…`, every running/stopped container image and other
projects remain. All **10 containers / 14 volumes**, including Demo IDs/images/
start timestamps, match before and after. No volume/container/cache pruning;
shared logical image sizes do not establish physically reclaimed bytes.

No real Google/GCP, reviewed managed-Demo cutover, connected OKX test or production
deployment is included. [Source/TDD verification](../implementation/MM_RUNTIME_FOLLOWUP.md)
retains the full PG attempt's obsolete query-count assertion failure and focused
correction separately from this image gate.
