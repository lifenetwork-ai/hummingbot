# Quote-runtime image verification — 2026-11-08/09

**The new full load gate fails the original burst latency budgets.** Warmup,
mixed, sixty-minute Random soak and all five fault assertions pass. The soak has
zero unexpected API errors, matching fill/fee/position records and bounded
retained growth. It does not erase the burst failure or prove continuous quotes.
The corrected `all` E2E attempt passes fourteen groups, then fails a nonzero
container shutdown in inventory skew. All thirteen targeted/remaining groups subsequently pass, covering twenty-seven
unique available groups across attempts; the failed `all` attempt is retained.

## Image and unchanged workload

- Image `sha256:96368970cb862da4dae1877f9f4e2808a8f4674b855226fd16f956f5a86f503a`,
  tag `hummingbot-cloudrun:quote-runtime`, build **`8544488ef`**, amd64,
  **1,374,595,562 bytes** against the unchanged 1,536 MiB budget.
- Immutable local registry manifest
  `sha256:bfa3d7774efc93b0a6ff77db6163bc09494f3d087024110dd42bac9e97a23425`.
  The local tag and registry reference identify one candidate image.
- **1,225 packaged source/config hashes** verified against the build tree;
  **1,254 captured source/fixture hashes unchanged** through load. Four packaged
  counter/behavior contracts also pass. Initial supplemental helper drafts failed
  on their own missing path/config ID; correcting them did not change the image.
- The source phase has **1,010 native passes + 9 subtests**. Component savings and
  unchanged-path overhead remain in the [source report](../implementation/MM_RUNTIME_FOLLOWUP.md).
  These isolated savings do not establish a whole-system latency improvement.
- Unchanged command: `all --operations --security --incident-diagnostics --cpus 2
  --daily-cache-ttl-seconds 15 --keep-going`. Continue-after-failure runs later
  phases but preserves the nonzero result. No budget, pool, timeout or CPU change.
- 100k orders/100k fills/100k audit events; 20 main + 5 auxiliary reads/s,
  100 book updates/s, one simulated fill/s, 500-update/s burst. Bot 2 CPUs/2 GiB;
  PostgreSQL 2 CPUs/512 MiB; simulator/client each 1 CPU/512 MiB.
- Full trace, accelerated refresh and the separate MM activity overlay are off.
  Bounded incident capture is on. Host is arm64 Docker Desktop emulating amd64.

Raw load: `build/mm-load/20261108-231941-6b7391/result.json`; build, source checks,
original/corrected E2E logs and power samples:
`build/quote-runtime-release-20261108/`.
[Portable evidence](mm-runtime-followup-load-evidence.json) retains failed attempts
and separates image source, mounted fixtures and observed acceptance.

## Healthy load: burst remains failed

| Window | Seconds | Main reads | Main p95 / p99 (ms) | Unexpected errors | New simulated fills | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| warmup | 300 | 6,000 | 69.20 / 391.54 | 0 | 300 | pass |
| mixed | 900 | 18,000 | 115.33 / 491.37 | 0 | 900 | pass |
| burst | 90 | 1,800 | 458.73 / 1,084.01 | 0 | 90 | **fail** |
| Random soak | 3,600 | 72,000 | 90.34 / 547.30 | 0 | 3,594 | pass |

Burst auxiliary p99 is **1,151.07 ms**. Main/auxiliary exceed the original
1,000 ms limit by **84.01 / 151.07 ms**; the only declared violations are
`read_p99` and `auxiliary_read_p99`. All **97,800 main / 24,450 auxiliary** reads
complete HTTP200 without unexpected stale responses. Burst backlog recovery is
**2.79 s / 10 s**; the client-request drain metric is separately 0.057 s. Query
count, page size, command timing and account-read budgets retain their results.

Final **4,884 unique simulated fills** reconcile exactly with recorder quantities,
fees and held/active positions, together with two identified funding bills and
six daily snapshots. These are internal simulator fills, not OKX Demo trades.
Random admission gaps retain the existing observed-rate policy; fixed mixed and
burst retain their original rate checks.

All **58 soak samples** are running, supervisor/engine/API PIDs **1/32/33**,
maximum sample gap **65.55 s**. Retained growth is **26.09 MiB / 64 MiB** on
`cgroup_current_minus_file_plus_shmem`; raw cgroup growth is **34.21 MiB**.
Engine FDs 26→28 (range 26–34), API 23→23; threads remain 14/16. The finite soak
passes its retention budget; it does not guarantee unlimited leak-free operation.
Burst samples intentionally include stopped states during Stop/Start commands.

## Recovered probes and comparison

| Window | Previous accepted image p99 (ms) | New image p99 (ms) | New recovered probes: engine / API |
| --- | ---: | ---: | ---: |
| warmup | 312.31 | 391.54 | 0 / 0 |
| mixed | 366.46 | 491.37 | 1 / 1 |
| burst | 168.04 | 1,084.01 | 2 / 0 |
| soak | 287.63 | 547.30 | 7 / 1 |

**12 healthy probe transitions recover within their windows**, about
**1.031–1.248 s**, without healthy child replacement. These are monotonic
supervisor observation intervals, not measured quote downtime. This baseline
has no independent venue-quote/tick activity stream, so stable PIDs and reconciled
fills do not prove continuous quoting during every interval.

Burst captures include a gen2 collection of **290.93 ms** near a 657.61 ms
inclusive observer span and 319.92 ms recorder span; another episode includes
31.68 ms gen0 collection and a 198.41 ms recorder span. Async inclusive timings
can include other loop work. A sampled SQL/checkpoint stack describes its sampling
instant, not the duration of the whole delay. Temporal overlap does not establish
one exclusive cause for the API p99 failure or historical probe clusters.
At soak end, sink drops/suppressed incidents/skipped metric exports are zero;
bounded child-span omissions remain **61,348 engine / 7,554 API**.

The prior accepted `a6fe88c0…` report/evidence remains in Git **`8544488ef`** at
these paths. Its AC-only host conditions differ from this run, and the cold
rebuild selected current dependencies from the existing unpinned environment.
Installed distribution versions and conda manifests are recorded separately
from runtime import smoke tests (Python 3.13.18, NumPy 2.5.4, Pydantic 2.14.3,
SQLAlchemy 2.1.8).
The table records an observed regression; neither the new source changes nor
battery use is established as the sole cause.

## Offline E2E and a corrected fixture

The first `all` attempt passes six scenario groups, then fails at
`initial-position-restart`, before sending any venue orders. Its live-mode host
fixture omitted three dummy perpetual credentials; the demo constructor already
supplied its dedicated sets. The service correctly returns
`configuration_unavailable`. That fixture originated in `1ee10e01bc`, before the
new production optimizations.

TDD reproduces **live fail / demo pass**, then **14 related native tests pass**
after the fixture supplies the same dummy perpetual fields as other both-market
scenarios. Commit **`556d30bb7`** changes the host runner and its regression test;
it does not alter engine/controller/recorder code or weaken credential checks.
The first failed E2E remains retained. The corrected `all` attempt passes fourteen
groups, including the repaired initial-position case, then fails inventory skew
at the original `ExitCode == 0` shutdown assertion before its restart. Trading
core logs shutdown complete; no ERROR/CRITICAL is retained in that container log.
The original Docker exit code/timing was not saved before cleanup, so its unique
cause is **unproven**. This failed attempt remains failed.

The runtime image still matches build `8544488ef`. `.dockerignore` excludes the
host E2E runner from the image; all 1,225 packaged hashes match fixture checkpoint
`683773d34`, before the later runtime signal fix. The external fixture hash belongs separately to `556d30bb7`. No compatible prior image exists in the current daemon,
so upgrade/rollback E2E remains **unrun**, not passed by comparing the candidate
to itself. Neither offline scenario coverage nor historical Demo acceptance
implies connected Demo validation of this candidate.

## Faults, logs and host scope

All five fault assertions complete successfully: feed loss, owner restart, queued
expiry, database delay and database failure. Raw fault `unexpected_errors` are
**0 / 251 / 0 / 279 / 365** respectively; expected HTTP503/transport failures
remain separate from healthy acceptance. All eight ERROR records occur within
the two injected DB windows. Healthy windows and inter-window/preparation structured logs
contain no ERROR/CRITICAL; six child starts precede measurement, and replacements
occur only inside injected fault windows. Two fault probe failures lack recovery
markers; do not pair across child replacement to invent a recovery interval.

Battery starts at 82%; first observed AC attachment is
**2026-11-08T17:16:58.420909Z**, at 53%, partway through soak. Warmup/mixed/burst
run on battery; soak crosses to AC. Low Power Mode is observed, not changed.
Scoped idle-sleep inhibition does not prevent lid closure or forced sleep.
Host observations show no recorded thermal/performance warning; that does not
establish an uncontended host or exclusive power causation.

The daemon initially has no bot containers or prior bot/rollback image. Existing
unrelated images and `onchain-handler_pgdata` are retained. No image pruning,
real OKX/Demo trade, real Google/GCP, deployment or existing DB repair occurs.
Final inventory retains both unrelated stopped container identities/states,
all four pre-existing image IDs and the one existing volume. Harness containers
and their temporary volumes are removed.

Host-only diagnostic commit `683773d34` adds bounded Docker State capture before
cleanup, including after a polling failure. Red 4 failures / related Green 18
passes; timeout 9.5 s, exit-zero/OOM assertions and runtime image stay unchanged.
It excludes environment and health-response bodies. All thirteen targeted/remaining groups pass on that same image: inventory-skew,
spread-policy, volatility-defense, four managed lifecycle and six cleanup faults.
The captured first inventory shutdown is exit0/non-OOM in 1.30 s. Combined with
the fourteen earlier groups this covers twenty-seven unique available groups,
but the failed `all` attempt remains failed. The diagnostic is not a fix for the unexplained exit.

**Still open:** the new full-performance gate (burst p99), the inventory shutdown
observation, absent previous-image
upgrade coverage, exclusive probe-cause attribution and the production
continuous-quote SLO. See the [remaining-work register](../../docs/plans/README.md).

## Confirmed source follow-up, not packaged here

Commit `1916e048e` fixes a real-signal/Event-lock hazard: the plain Event signal
handler can re-enter the lock held by `Event.wait()` and reach the hard deadline
with exit1. Red1 / related Green75 confirm the fix. It predates the quote changes
(branch `6de4001cde`, 2026-10-29). The supervisor now observes a request flag at
existing 50 ms waits/child-launch checks, leaving async wakeup and deadlines intact.
No new thread is added. This is a possible mechanism for the observed exit, not
its unique historical proof, and is unrelated to any proven p99 cause.

Image `96368970…` does not contain this runtime fix. New-image native/E2E/full-load
qualification is pending in `build/shutdown-signal-release-20261109/`. The failed
burst and failed `all` attempts above must not inherit a later image's pass.
