# Full-image MM soak retest — 2026-10-26

The unchanged sixty-minute load gate **passed** on the rebuilt Cloud Run image:
**72,000 HTTP 200 responses, zero errors, p95 67.77 ms and p99 355.03 ms**.
All independent-ledger, position, funding and resource-growth assertions passed.
The runner exited zero with no budget violations.

The user accepted this passing gate on 2026-10-26 and requested closure without
further timeout reproduction. The acceptance and reproduction follow-up are
closed. The historical ten-timeout cluster was not reproduced, and its exact
cause is not established; accepted closure does not change that evidence.

[Portable evidence](life-mm-soak-retest-evidence.json) records the full artifact,
measurements, source/fixture/trace hashes and explicit closure fields.

## Unchanged workload and budgets

The existing `--profile soak --trace --keep-going` runner executed one complete
3,600-second window. It used 100,000 historical orders and 100,000 historical
fills, ten FE workers offering 20 reads/s, 100 aggregate book updates/s and one
synthetic fill/s. Spot and perpetual ran together, with random profile steps
every 600 seconds in a finite two-hour session. Containers retained the original
limits: bot 1 CPU/2 GiB, PostgreSQL 2 CPUs/512 MiB, client and simulator each
1 CPU/512 MiB, with equal memory/swap limits.

Aggregate read budgets remained p95 ≤500 ms, p99 ≤1,000 ms and zero unexpected
errors. Original command, backlog, RSS, query-count, exchange-call and soak-growth
checks were unchanged. No failure was filtered, retried away or hidden by a
relaxed threshold.

| Observation | Prior traced soak | Full-image retest |
|---|---:|---:|
| Duration | 3,600 s | 3,600 s |
| Reads completed | 72,000 | 72,000 |
| Unexpected client errors | 10 timeouts | **0** |
| Aggregate p95 | 345.91 ms | **67.77 ms** |
| Aggregate p99 | 1,250.96 ms | **355.03 ms** |
| Unmatched fills / positions | 0 | **0** |
| Outcome | Failed errors/p99 | **Passed all existing checks** |

The old and new random draws differ, and the test wrapper now measures additional
stages. This is an acceptance comparison, not an isolated causal experiment.
The short controlled traced/untraced probes and serializer CPU A/B are documented
in the [short diagnostic review](../investigations/life-mm-short-repro-review.md).

## Integrity, lifecycle and resources

- All **3,600** unique injected fills, prices, exact fees and base/contract units
  matched recorder rows. Actual perpetual positions matched the independent
  simulator and the engine's held/active exposure. Both identified funding bills
  matched without duplicates; daily counts and balance snapshots passed.
- All 72,000 reads returned HTTP 200. Achieved completion rate was 19.9999/s,
  with zero queued reads at generation end. Book rate was 99.961 updates/s.
  The existing random-soak protocol reports rates across handover pauses and
  bounded drain; the fixed mixed profile remains the sustained-rate gate.
- Read-only observations confirmed generations 0–5 and five completed handovers:
  `sideways → sideways → follow_token → follow_market → follow_market → sideways`.
  All three profile types appeared, with spot/perpetual active together. A new
  boundary near measurement end is not counted as another fully observed step.
- **58** minute samples satisfied soak checks. Peak bot RSS was **351.93 MiB**;
  first/last five-sample median RSS grew **28.59 MiB**, below the 64 MiB budget.
  Median book backlog stayed **2 → 2**; median checkout p95 changed
  **1.76 → 1.68 ms**, within the existing growth budget. No HALT was observed.
- Start/switch/stop completed within the existing 15/15/5-second limits.
  Recorded order/fill/performance query-count and exchange-account-call bounds
  passed. The database pool and persistence/fencing rules were unchanged.

Per-route metrics remain visible in the JSON. Daily-statistics p95 is still
630.30 ms and p99 721.14 ms; the declared gate is aggregate latency. These results
do not assert that every individual request or route stays below 500 ms.

## Artifact and diagnostics

This is a **full `Dockerfile --target cloudrun` build**, not the earlier diagnostic
overlay. Its source commit is `8cfc9d56c244ab0a9526c71eece2d4b1ebdb3af7`.
Twenty-three runtime/SQL source hashes were verified inside the image before the
run. Among the previously tracked runtime files, only `admin_models.py` changed
from the failed soak: datetime-only Pydantic serialization and nested UTC handling.

Image ID:
`sha256:518e45bddf878d66d4439f19cc4d9ee9a06f151b8e453c1489af41784442e53d`.
Pinned registry manifest:
`sha256:751be9c5a0d7ef895193bf7b3d86b400497cfc5c09d99f8987047267142708d8`.
Image size: 1,364,533,218 bytes. The host remains ARM with amd64 application/client
emulation; PostgreSQL and Docker resource identities are in the evidence.

Tracing retained 14,136 in-window PostgreSQL samples and 15 loop-stall samples.
The database sampler recorded **zero errors**; recorded stream-drop counters
were zero. The bounded 128-child-span roots omitted 3,017 child spans from
`service.observe_once` across the whole file (2,784 in overlapping workload
roots). HTTP request roots did not lose child spans. Raw artifacts remain under
`build/mm-load/20261026-112040-e7529b/`; server counters are periodic snapshots,
not a graceful-teardown census.

The evidence labels final runtime distributions as **rolling**; they are not
whole-hour percentiles. Whole-hour read latency comes from all scheduled client
requests. Retained traces still include individual handlers above one second,
so the successful p99 budget is not a maximum-latency guarantee. Thread CPU is
inclusive; async CPU and nested wall times must not be added.

## Checklist decision

- **Closed:** Red/Green verification for the confirmed UTC/serialization change.
- **Closed:** full-image, unchanged sixty-minute zero-error acceptance gate.
- **Closed by user decision (2026-10-26):** no further timeout-cluster reproduction
  is required after the passing gate. The short parent probes and this retest
  did not reproduce it; its historical cause remains unproven.

The [implementation plan](../../docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
records the passing acceptance and the user-approved closure of further
reproduction separately from the unproven historical cause. No additional hour-long untraced control was run.
This verification used the local simulator and disposable PostgreSQL; historical
OKX Demo evidence retains its original image and scope.
