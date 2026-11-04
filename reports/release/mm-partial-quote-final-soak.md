# Partial-fill quote candidate: final soak and controller trace

2026-11-05 (Asia/Saigon). **The unchanged standalone 60-minute gate passes on
candidate `354fec1e`.** The bounded controller diagnostic also passes. This
follow-up adds evidence, with no additional shipping runtime change.

[Evidence and artifact hashes](mm-partial-quote-final-soak-evidence.json),
[Red/Green fix and matched short comparison](../performance/mm-partial-quote-size.md),
[checklist](../../docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md#fine-controller-trace-and-candidate-hour--requested-2026-11-04).

## Candidate and unchanged workload

Image `354fec1e500524a8e38e512da636ccfdde0dfdf2a1a905ea78862fb29ad6d34c`
contains the partial-fill quote-size fix from `905bff7e3`. The reviewed derived
image preserves every parent layer and changes exactly one of 1,316 application
files, `controllers/market_making/life_mm.py`. This is its own candidate artifact;
the canonical Dockerfile has not been rebuilt in this phase.

The ordinary hour uses the same fixture and measurement-source hashes as the
prior `086454de` hour, with the candidate image as the runtime difference:
100,000 historical orders/fills, original 20 primary plus two auxiliary reads/s,
100 book updates/s, six 600s Random steps, TTL 15, enhancements and volatility
defense. Application limits remain two CPUs/2 GiB; PostgreSQL remains two
CPUs/512 MiB. This is a local amd64 image under emulation on an arm64 Mac.
No additional operations reads, fine trace, incident, GC or MM-activity tools
are enabled during the hour. No pool, deadline, ownership, risk or durability
setting changes.

Preparation stops the full disposable service before bulk history seed and
CHECKPOINT. Zero application connections, ownership locks and pending orders
are verified. Normal fenced startup advances owner generation 1→2 before
measurement. This is planned fixture preparation, not an engine failure.

## Unchanged 60-minute result

Measured window: **2026-11-04 16:36:04.490–17:36:04.490 UTC**.
Raw artifacts: `build/mm-load/20261104-233452-c23f36/`.

| Check | Result |
| --- | --- |
| Original gate violations | None |
| Primary / auxiliary HTTP 200 | 72,000 / 7,200 |
| Unexpected HTTP errors / stale responses | 0 / 0 |
| Primary / auxiliary API p99 | 126.88 / 76.41 ms |
| Exact unique synthetic fills, fees and positions | 3,594; matched |
| Identified funding bills / daily snapshots | 2 / 6 |
| Continuous minute samples / maximum gap | 58 / 65.50 s |
| Engine PID in all samples | 31; running |
| Retained-memory median growth / original budget | 27.59 / 64 MiB |
| Unexpected service failure / supervised child restart, including setup | 0 / 0 |

The Random draw actually selects follow_market, sideways, follow_market,
follow_market, follow_market, follow_token. All three profiles occur in this
draw; the unchanged acceptance gate does not force a particular random sequence.
The one-hour retention result is not proof against leaks at every longer duration.

### Probe and safety records remain visible

There are **four recovered engine-probe episodes inside the measured hour** and
one additional recovered probe during preparation. The four measured timed-out
requests take 301.66–303.90 ms; supervisor recovery-observation intervals are
1.016–1.077 s. Neither number is a measured MM outage duration.

All four measured intervals have local fill-processing logs and independently
timestamped simulator fills; three also have new simulator-accepted orders.
These witnesses establish some MM work during those intervals. The ordinary
hour does not record every tick or continuously sample both sides of the book,
so it does not prove continuous fresh two-sided quotes or every tick deadline.

Two ERROR records refer to **one locally rejected spot SELL**, at about +2,321 s:
`order_submission_blocked`, `submission_safety_blocked`,
`spread_policy_quote_rejected`. The existing final send gate rejects a proposal
that fails the current spread policy. The ID is absent from simulator accepted
orders, all orders and fills. Another spot SELL is accepted about 2.03 s later;
ten simulated fills occur in the next ten seconds. No service failure, HALT
event or child restart follows. The evidence retains both ERROR records; this
is not described as a zero-ERROR log run, nor is the block silently removed.

The first evidence publisher copied a prior run's assumption that every ERROR
submission block occurs before measurement. That assertion fails before export.
After independent ledger/log review, the publisher explicitly verifies the two
records, single rejected ID and subsequent progress. Its first source and
failure note are retained. **The workload, original gates and raw passing result
are unchanged.**

## Bounded fine controller diagnostic

A separate 180s candidate run passes 3,960 HTTP 200 responses, zero unexpected
errors/stale, and 180 exact synthetic fills/fees/positions. Primary API p99 is
267.19 ms. Its 180 completed ticks have maximum execution 69.92 ms; 355 controller
cycles have maximum execution 51.67 ms. No in-window probe failure is recorded.
Raw artifacts: `build/mm-load/20261104-232346-1f16ea/`.

Temporary fixture wrappers reuse the existing timing/tracing primitives to
separate reference, inventory, guard, checkpoint, action/refresh, snapshot and
publication work. They preserve decisions and exception propagation and capture
no locals or SQL parameters. A separate bounded 8 MiB archive retains slow roots;
each root retains at most 16 child spans. The last metrics export records zero
file drops and zero queued records. Export counters can precede the final events;
omitted child spans remain explicit. This instrumentation adds overhead and is
absent from the ordinary hour, whose fixture hashes are independently checked.

| Observation | Supported interpretation |
| --- | --- |
| Cold NATR/reference spans exceed 400 ms before measurement | Startup CPU work; not a reconstruction of the historical in-window 568.53 ms controller spike |
| Seeded expiry pulse: 290.23 ms wall / 283.17 ms thread CPU | Synchronized synthetic-history expiry has a measurable CPU cost; not proof of the old controller cause |
| Publication: 223.97 ms; same-worker generation-2 GC: 214.96 ms | GC overlaps this current slow publication; its retained SQL spans are about 1–2 ms |
| Another observer root: 402.89 ms | Attribution remains incomplete; bounded spans and inclusive async CPU do not justify assigning all time to that method |
| Current-catalogue session validation, 20 iterations | Median 1.04 ms, cold maximum 26.07 ms; this sample does not justify adding a cache |

The benchmark first fails module discovery, then succeeds with the correct
`PYTHONPATH`; the first log is retained. No runtime or workload correction is
hidden in that retry. Post-restart scalar distributions include pre-window
setup, and the slow-root archive is a selected subset; neither is presented as
an unbiased in-window component p99.

**The historical controller spike is not reproduced or exclusively explained.**
No additional avoidable runtime defect is established by this trace, so no
speculative cache, GC tuning, pool increase or deadline relaxation is introduced.

## Actual Demo restoration and remaining scope

A private backup precedes each isolated run. Actual Demo is restored **stopped
on its prior `086454de` image**, with owner available, no HALT, pending mutation
or unresolved order. All four ledger counts and hashes match: 3,513 orders,
23 existing fills, three positions and 2,848 executors. No actual Demo order,
database maintenance/reset, candidate upgrade or deployment occurs in this phase.
All 3,594 measured hour fills and 180 diagnostic fills are offline simulations.

This completes the requested bounded trace and standalone candidate-hour
checklist. It does not claim a new full R1/R2 matrix, canonical image rebuild,
full PostgreSQL suite at this source, active candidate-image OKX Demo acceptance,
production deployment or exclusive historical-outlier attribution. The prior
528 native checks plus two subtests and two targeted packaged E2E groups retain
their documented scopes in the fix report.
