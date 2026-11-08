# Signal-safe runtime image: local verification — 2026-11-09

**Image `b090430c…` passes all requested local checks:** canonical native,
packaged-source/signal contracts, all 27 available E2E groups in one `all`
execution, all four load windows, sixty-minute Random soak and five fault
assertions. Three healthy engine probes still recover after about 1.06–1.12 s.
A separate distinct-image technical upgrade/takeover/rollback also passes.
Real Google/GCP, connected Demo, a qualified production rollback and production
quote-coverage acceptance remain separate.

## Confirmed bug and source scope

SIGTERM can interrupt `threading.Event.wait()` while its non-reentrant condition
is held. The old handler calls `Event.set()` and tries the same lock, reaching
the hard deadline with exit1. This branch predates the quote optimizations:
`6de4001cde` (2026-10-29), original deadline `9f7ea36c4b` (2026-10-24).

Fix **`1916e048e`** records a request flag; the supervisor checks existing 50 ms
waits and child-launch boundaries. Ordinary cleanup still notifies Event waiters;
async self-pipe wakeup, hard deadline and child/recovery budgets remain. No new
thread or trading-loop behavior. Red1 / related Green75; the canonical expanded
regression now passes **2,861 tests + 16 subtests**. These counts overlap.

A real-SIGTERM contract in both Linux images confirms old `96368970…` exits1
without returning from the handler; new `b090430c…` returns and exits0. Neither
contract has network access. The new one-attempt E2E inventory case also passes
shutdown/restart: exit0/non-OOM, 1.308 s to the saved exit observation, 1.34 s
including the helper's final inspection/artifact work.

The mechanism is proven fixed; the exact cause of the predecessor's observed
nonzero exit remains unproven because its Docker State was removed before capture.
Host fixture `683773d34` now saves bounded State/timing before cleanup without
Config.Env or health bodies. Earlier `556d30bb7` fixes dummy perpetual credentials
missing from the seed fixture; the runtime correctly rejected that bad fixture.
[Source/TDD and component costs](../implementation/MM_RUNTIME_FOLLOWUP.md).

## Candidate and measurement

- Build **`d0791e563`**, runtime fix `1916e048e`; image
  `sha256:b090430c8ee89f61e189f1dc50c22dcccd00c32208e130deb4701446980b1d3d`,
  tag `hummingbot-cloudrun:shutdown-signal`, **1,374,595,999 bytes** / 1,536 MiB budget.
- Immutable manifest
  `sha256:de4415f8d095bfd01cbb2dd40974ca7e7878666e398181615b258d3835cc861c`.
  All **1,225 packaged hashes** match. Compared with `96368970…`, the only source
  differences are `service.py` and `service_processes.py`; selected installed
  Python/NumPy/pandas/Pydantic/SQLAlchemy/psycopg2/aiohttp/cryptography versions match.
- **1,254 source/fixture hashes remain unchanged** through measurement. The host
  E2E runner is excluded by `.dockerignore`; its own hash is recorded separately.
- Same `all --operations --security --incident-diagnostics --cpus 2
  --daily-cache-ttl-seconds 15 --keep-going`. No duration, latency, pool, timeout,
  GC or resource-budget relaxation. Continue-after-budget-failure would retain
  a nonzero result; this attempt has no declared violation.
- 100k orders/100k fills/100k audit events; 20 main + 5 auxiliary reads/s,
  100 book updates/s, one simulated fill/s, 500-update/s burst. Bot 2 CPUs/2 GiB;
  PostgreSQL 2 CPUs/512 MiB; simulator/client each 1 CPU/512 MiB.
- arm64 Mac emulating amd64; all 281 recorded power samples are AC. Existing Low
  Power Mode is observed, not changed. No recorded sleep-sized observation gap;
  maximum host sample gap 30.26 s. This does not prove an uncontended host.
- Full trace, accelerated refresh and the separate MM activity overlay stay off.
  Bounded incident diagnostics remain on; they do not measure continuous quotes.

Raw E2E `build/e2e-offline/20261109-015252-6f8096/result.json`; load
`build/mm-load/20261109-023821-5580d7/result.json`; build/analysis
`build/shutdown-signal-release-20261109/`.
[Portable evidence](mm-runtime-followup-load-evidence.json).

## Unchanged healthy gates

| Window | Seconds | Main reads | Main p95 / p99 (ms) | Unexpected errors | New simulated fills |
| --- | ---: | ---: | ---: | ---: | ---: |
| warmup | 300 | 6,000 | 56.57 / 569.69 | 0 | 300 |
| mixed | 900 | 18,000 | 58.37 / 273.48 | 0 | 900 |
| bursts | 90 | 1,800 | 63.04 / 297.81 | 0 | 90 |
| soak | 3600 | 72,000 | 65.64 / 313.00 | 0 | 3,595 |

All **97,800 main / 24,450 auxiliary** healthy reads complete HTTP200 without
unexpected stale responses. Burst auxiliary p99 **133.70 ms**;
backlog recovery **2.14 s / 10 s**, with the separate client-request
drain metric 0.031 s. Query/page, account-read and command budgets pass.
Random admission gaps keep the original observed-rate policy; fixed mixed/burst
retain their rate checks. Intentional Stop/Start samples in burst are not an
unexplained shutdown.

Final **4,885 unique simulated fills**, exact quantities/fees and
held/active positions reconcile; **2 identified funding bills / 3 daily balance
snapshots**. These are simulator records, not OKX Demo trades. The snapshot table
uses explicit reporting-day/currency keys; observation timestamps remain UTC.

All **58 soak samples** are running, PIDs **1/32/33**, maximum gap **65.59 s**.
Retained growth **30.33 MiB / 64 MiB** on
`cgroup_current_minus_file_plus_shmem`; raw cgroup growth **32.54 MiB**.
Engine FDs 26→27 (range 26–33), API 19→22 (range 19–23); threads stay 14/16.
The finite retention gate passes; it does not prove unlimited leak-free execution.

## Probes, logs and the failed predecessor

Healthy captures retain **three engine failures/recoveries**: warmup **1.116 s**,
soak **1.083 / 1.057 s**; none in mixed/burst. No healthy child replacement.
These are monotonic supervisor observation intervals, not measured MM quote
stoppage. There is no independent venue-quote activity overlay in this baseline.

Healthy/preparation/inter-window structured logs contain **zero ERROR/CRITICAL**.
Six child starts precede measurement; eight ERROR records and two scheduled child
restarts belong to the two injected DB fault windows. At soak end, sink drops,
suppressed incidents and skipped metric exports are zero. Bounded child-span
omissions remain **61,571 engine / 7,581 API**; temporal spans/stacks/GC overlap
cannot prove one exclusive historical cause.

| Window | Failed predecessor `96368970…` p99 (ms) | Current p99 (ms) |
| --- | ---: | ---: |
| warmup | 391.54 | 569.69 |
| mixed | 491.37 | 273.48 |
| bursts | 1084.01 | 297.81 |
| soak | 547.30 | 313.00 |

The preceding burst failed main/auxiliary p99 1,084.01/1,151.07 ms; its corrected
`all` E2E failed inventory shutdown after fourteen passed groups, followed by
thirteen passing targeted groups. Both failures remain unchanged in Git
**`d0791e563`** at these report/evidence paths. Prior accepted `a6fe88c0…` evidence
remains in Git **`8544488ef`** at the same paths.

The old burst ran on battery; its soak crossed to AC. The current observed run
uses AC and fewer probes, but warmup p99 and retained growth are higher. This is
not uniform improvement. The shutdown-only correction is not a proven cause of
healthy API/p99 changes; neither power nor any other single cause is established.

## Fault checks and limits

All five assertions pass: feed loss, owner restart, queued expiry, DB delay and
DB outage. Raw fault `unexpected_errors` remain **0 / 301 / 0 / 294 / 387**;
expected HTTP503/transport failures are retained separately from healthy gates.
The fixtures verify explicit recovery and unresolved-effect HALT behavior rather
than bypassing trading authority.

Both unrelated stopped containers retain their IDs/states; existing image IDs
and the one pre-existing volume remain. Harness containers/temporary volumes are
removed. Candidate and comparison images are retained. No real OKX/Demo, real
Google/GCP, production deploy, existing DB repair or image prune occurs.

The 27-case all command omits `--previous-image`, so its raw
`release_scope_complete` flag remains false. A subsequent standalone upgrade
scenario passes with distinct, schema-compatible `96368970…` → `b090430c…` →
`96368970…`: additive nullable test column, ownership takeover, one deduplicated
fill/fee and retained data through rollback. This gives 28 local groups across
two commands. It does not qualify the failed predecessor as a production
rollback, nor verify every historical release/migration path.
Historical cause attribution, real external acceptance and the production
quote-coverage SLO remain in the [register](../../docs/plans/README.md).
