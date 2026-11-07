# Combined runtime image: full local load gate

Image **`a6fe88c0…` passes** the unchanged baseline, sixty-minute Random soak,
five fault windows and four targeted E2E groups on their first attempts. It
packages the fresh write-fence projection and reporting-copy optimizations.
**Probe frequency and burst p99 regress** versus the preceding accepted image;
this pass does not close probe attribution or establish continuous quoting.

The later [code audit and ABBA comparison](../implementation/MM_RUNTIME_FOLLOWUP.md#combined-image-regression-audit--2026-11-07)
retain this full gate unchanged. They identify and fix a test incident-recorder
lock dependency, without establishing it as the cause of the earlier probe
increase. New short diagnostics do not replace this hour-long acceptance scope.

## Candidate and scope

- Tag `hummingbot-cloudrun:runtime-copy`; amd64; **1,374,449,982 bytes** (~1.37 GB).
- Image `sha256:a6fe88c0bba185d27ed68d1fbc1f43dfa39c5a560c80df1f28e720f2afeb16b2`.
- Manifest `sha256:7e615f650e8c7a6c5265bef45f685e74aae7b2096ebdf5d895916a8deb20e6aa`.
- Build commit `745a86e3c`, including write-fence projection `5c421b121`, reporting
  copy `745a86e3c`, ownership reads `d9488143d` and recorder `e030cfcdc`.
  All **1,224 packaged source/config hashes** match and remain unchanged through
  measurement. No probe, I/O, retry, pool, cache, CPU or load budget changes.
- Full result `build/mm-load/20261107-184132-b32ad7/result.json`;
  preparation/E2E/diagnostics/cleanup `build/runtime-copy-release-20261107/`.
- [Portable evidence](mm-runtime-followup-load-evidence.json) records image,
  source/fixture/log hashes, raw artifact hashes, incidents, role resources,
  preceding gate comparison and guarded cleanup. The complete runner exits zero.

The unchanged `all --security --operations --incident-diagnostics --cpus 2
--daily-cache-ttl-seconds 15` baseline uses 20 main reads/s, five auxiliary reads/s,
100 book updates/s, one synthetic fill/s and a 500-update/s burst. It seeds
100,000 orders, 100,000 fills and 100,000 audit events while stopped. Bot limits
are two CPUs/2 GiB, PostgreSQL two CPUs/512 MiB; amd64 is emulated on an arm64 Mac.
Full tracing and MM activity diagnostics remain disabled in the baseline;
incident capture is bounded. Separate diagnostic runs are identified below.

## Healthy load

| Window | Seconds | Main reads | p95 / p99 (ms) | Unexpected errors |
| --- | ---: | ---: | --- | ---: |
| warmup | 300 | 6,000 | 57.01 / 315.63 | 0 |
| mixed | 900 | 18,000 | 62.84 / 353.23 | 0 |
| bursts | 90 | 1,800 | 110.93 / 503.57 | 0 |
| soak | 3,600 | 72,000 | 68.88 / 406.14 | 0 |

All **97,800 main / 24,450 auxiliary** reads return HTTP200, with no unexpected
stale response or declared budget violation. Healthy parsed logs contain zero
ERROR/CRITICAL and zero child lifecycle events. Burst drain is **3.31 s / 10 s**.
**4,885 unique simulated fills**, exact fees and positions reconcile, with two
identified funding bills and three daily snapshots. These are simulator fills,
not transactions on OKX Demo. Random admission gaps retain the existing fill
policy; fixed mixed retains its original throughput checks.

All **58 valid soak samples** are running; maximum sampling gap **65.50 s** and
role PIDs **1/32/33** remain unchanged. Retained growth is **24.26 MiB / 64 MiB**
on the unchanged `cgroup_current_minus_file_plus_shmem` basis. Engine FDs 27→28
(range 26–36), API 24→25; engine/API threads remain 14/16. The finite resource gate
passes; it does not prove unlimited leak-free operation or uninterrupted quotes.

## Recovered probes and comparison

| Measured window | Role and episodes | Supervisor error→recovery interval |
| --- | --- | ---: |
| warmup | none | — |
| mixed | engine: 3 | 1.024–1.060 s |
| bursts | none | — |
| soak | engine: 11 | 1.048–1.454 s |

All fourteen recover within their measured windows, without a healthy engine
restart. No additional probe marker appears between healthy windows in retained
records. An injected DB-delay marker has no retained recovery transition and is
reported separately, not paired across replacement to invent an interval.

These intervals are supervisor observations, not measured quote downtime. The
baseline does not contain MM activity records, so its healthy PIDs and reconciled
ledger cannot establish continuous quoting during every probe. At soak end,
engine context capture reaches 32 and suppresses 4 captures; sink drops are zero,
but bounded span/history recording is incomplete. The evidence retains these
limits and the all-phase marker inventory.

Compared with accepted rollback `e00cef38…`:

| Measurement | Previous | Current |
| --- | ---: | ---: |
| Warmup p99 (ms) | 327.65 | 315.63 |
| Mixed p99 (ms) | 387.34 | 353.23 |
| Burst p99 (ms) | 267.47 | 503.57 |
| Soak p99 (ms) | 410.20 | 406.14 |
| Burst drain (s) | 0.95 | 3.31 |
| Retained growth (MiB) | 28.59 | 24.26 |
| Soak engine probes | 5 | 11 |
| All measured-window probes | 6 | 14 |
| Additional probe between healthy windows | 1 | 0 |

Warmup/mixed/soak p99 and retained growth improve observationally; burst latency,
drain time and measured probe frequency worsen, within existing budgets. The
host is not a fully controlled paired experiment. **No uniform or causal
whole-engine speedup is claimed.** Probe/burst regressions remain visible in the
[remaining-work register](../../docs/plans/README.md#policy-and-evidence-still-unresolved).
Previous image evidence remains in Git **`745a86e3c`** under this report's path and
the corresponding evidence JSON. The still earlier shutdown poll failure and its
unchanged retry remain in Git `ff6b85ded`; this new pass does not erase them.

## Separate MM activity diagnostic

A **300-second** diagnostic on the same image uses the original 60-second refresh
and adds `--mm-activity`. Result:
`build/mm-load/20261107-183445-413329/result.json`. It passes with p99 **442.35 ms**,
zero unexpected API errors and 300 exact simulated fills. Two engine probe
transitions have direct, aligned observations:

| Probe interval | Completed tick/controller cycles inside interval | Venue observation inside interval |
| --- | ---: | --- |
| 1.186 s | 5 | Both spot/perpetual have 4 working orders, two-sided |
| 1.040 s | 3 | Both spot/perpetual have 4 working orders, two-sided |

This establishes engine progress and resting orders at those observed points.
It does not prove that every instant was covered. Maximum controller start gap
near the first transition is **1.583 s**, while the strategy tick gap there is
**1.001 s**; some scheduling delay remains. Across the whole window: 300 completed
ticks, 596 controller cycles, 32 submit returns and 32 cancel returns, all successful.
Maximum connector-call durations are **318 ms submit / 241 ms cancel**; a return
is not independent proof of an exchange acknowledgement at every instant.

There are **298 aligned venue point samples** per market: spot 296 two-sided,
1 one-sided, 1 empty; perpetual 295 two-sided, 3 empty. All five non-two-sided
market observations have refresh stop proposals and submit/cancel activity within
±2 seconds. Isolated empty samples give no measured continuous absence duration;
`max_empty_sample_span_ms=0` does not mean zero actual quote gap. Point samples,
bounded traces and the shared simulator kernel clock cannot establish a numeric
production quote-coverage SLO or remote OKX latency.

One cumulative `SessionSubmissionBlocked` occurs during diagnostic startup,
about 9.7 seconds before the measured window; it is retained in the evidence.
Window-scoped submit returns contain no exception. Do not combine cumulative
startup counts with measured counts. MM/venue sinks report no overflow or observer
errors. This diagnostic does not add activity proof retroactively to the full soak.

The [FE/operator quote review guidance](../../docs/fe-integration.md#reviewing-quote-availability)
separates replacement progress, deliberate guard restrictions and unknown/stale
observations. The existing 120-second warning is not an agreed quote replacement
SLO; numeric production policy remains open.

## E2E, enhancement and fault scope

Availability, drawdown, MM recovery/DB restart and initial-position restart pass
on the same image. Each includes runtime ABI/JIT/TLS/SDK/size smoke. These are
four targeted groups, **not the full E2E/scenario/upgrade matrix**. Initial-position
restart retains two exact fills/fees/positions and a pinned session; measured
shutdowns are **2.034 / 1.378 s**.

A separate enabled-enhancement90 diagnostic passes: p99 **426.54 ms**, zero
unexpected errors, 90 exact simulated fills, two funding bills and three snapshots.
Local encryption, offline signed identities and security/operator reads are used;
Basic is disabled during the measured security workload. No real Google/GCP or
fallback to environment credentials is exercised.

All five faults pass: feed, owner restart, queued expiry, DB catalog-lock delay
and DB outage. The raw HTTP workload counters are deliberately retained:

| Injected fault | HTTP200 / HTTP503 main reads | Raw `unexpected_errors` counter |
| --- | --- | ---: |
| feed | 900 / 0 | 0 |
| owner restart | 639 / 26 | 287 |
| queued expiry | 1,500 / 0 | 0 |
| DB delay | 631 / 22 | 296 |
| DB outage | 552 / 33 | 382 |

The fault counters include connection failures and auxiliary requests; they are
not a count derived only from the HTTP status column. Fault classification
excludes them from healthy latency/error acceptance, without relabelling them as
zero. DB faults produce two ownership-lost failures, fail-closed starts awaiting
recovery, and supervised replacements. The existing simulator-only reviewed
recovery keeps HALT until the explicit fixture reset. All-phase parsed logs show
six pre-window child starts for preparation/seed services, no pre-window ERROR,
and the disclosed DB-fault errors/restarts; no additional transition restart is
hidden outside healthy measurement. Existing Demo is never targeted.

## Cleanup and remaining scope

After the successful runner exit, unused image **`837e7997…`** and its local tags
are removed without force. Unique images **15→14**. New candidate **`a6fe88c0…`**,
accepted rollback **`e00cef38…`**, all referenced running/stopped container images
and other projects remain. All **10 containers / 14 volumes**, including Demo
identities/images/start timestamps, are unchanged. No container/volume/cache
pruning; shared image sizes do not establish physically reclaimed bytes.

The [source/TDD report](../implementation/MM_RUNTIME_FOLLOWUP.md#reporting-snapshot-copy-cost--2026-11-07)
keeps component savings, rejected drafts and native/PG scopes separate from this
image gate. No actual OKX/Demo session, Google/GCP acceptance, managed-Demo cutover
or production deployment is included. No exclusive historical probe cause or
continuous-quote guarantee is established.
