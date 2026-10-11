# O.8 read-only worker checkpoint — 2026-10-11

## Verdict

**The worker mechanism is implemented; O.8.4 and O.8.7 remain open.** Neither thread nor process offloading closes the full-history latency gate. This report makes no offline-complete, demo, live-order or economic-readiness claim. The earlier [archive checkpoint](LIFE_OKX_O8_ARCHIVE_EVIDENCE_2026-10-11.md) remains historical evidence of its source snapshot.

## Implemented boundary

- `HistoryWorkerPolicy` requires explicit order/trade counts, snapshot-byte capacity and task timeout. A persistent executor admits one job at a time; additional requests fail immediately. The thread backend and the optional spawn-process backend use the same admission/deadline code. No unbounded waiting-job queue is introduced.
- The worker receives detached immutable recovery facts or immutable reservation-file bytes and a detached risk policy. It never receives a controller, connector, live journal, lock, reservation authority or send/cancel callback. History JSON normalization, freezing and digest computation, and cold reservation decoding run in the worker. Financial/provenance qualification and all durable writes remain on the event loop.
- Process startup must be requested explicitly with a startup timeout before work is eligible. It imports worker dependencies only; it does not prequalify the test's historical account cut. Process startup measurements are separate from the measured refresh/send cycle. The per-refresh timeout remains capped by the existing joint freshness deadline.
- Reservation decoding uses the same schema/financial validation as ordinary cold restoration. A worker candidate is immutable and bound to its source bytes, path and limits. Publication compares current exact file bytes and financial memory, rejects uncertainty, and cannot rebind a candidate to a different publication. Same-size edits with restored timestamps remain detectable.
- Joint history results recheck session/config/risk context, journal publication bytes, account observations, clock rollback, freshness and controller stop/session state. Current WAL/financial memory and runner scope are checked before financial qualification and settlement. No file lock spans a worker await.
- Timeout/cancellation does not stop an already-running job. Its slot remains occupied until it exits; no completion callback installs a cache, sends an order, releases funds or rearms a session. Late exceptions are observed. Stop closes admission without blocking the event loop on worker shutdown. Worker process death denies refresh and retains the existing claim.
- `JointRecovery(..., history_worker=worker)` is an explicit injection API. No YAML factory, automatic process startup, migration, live capacities or production order permission is enabled by this increment.

## TDD and functional evidence

The new suite initially failed collection because `history_worker` did not exist. The reservation predecode tests then produced seven failures before the snapshot decoder and integration were implemented. Regressions exercise blocked worker execution with a live event loop, saturation after timeout/task cancellation, late results, exceptions, invalid/mutable/oversized inputs, context/account/runner changes, same-stat journal edits, clock rollback, stale observations, uncertain reservations and controller stop.

The actual independent production watchdog starts an owned spot cancel while history encoding is blocked. Cancellation retains the WAL and shared-capital claim. Both backends run an actual affordable protected send; a cold constructor with an explicitly injected worker remains unarmed. Spawn transfer preserves nested immutability. A killed process denies reconciliation without releasing an already-sent order.

The final focused command passed **181 tests, 0 failed, 0 skipped**, with six existing warnings. It covers worker, joint recovery, O.8 review/continuous/verified-state/acceptance, reservation persistence and risk suites. Hooks passed for Python formatting/imports/lint, whitespace and key checks; YAML/ESLint had no applicable files. Exact commands, source hashes, outcomes and scale samples are retained in [machine-readable results](evidence/life_o8_worker_results.json).

The full candidate audit reports **2,131 passed, 5 failed, 0 skipped**, with 27 warnings, in 188.48 seconds. All setup/teardown phases passed. Failures are the legacy/segmented 10k and segmented-thread 1k/10k and segmented-process 10k cases. The full audit applies the previously frozen 5× instrumentation headroom to latency measurements; functional worker deadlines are not silently widened. It collected coverage but stopped before both pinned changed-line comparisons and a completion summary. No coverage percentage from an earlier snapshot is claimed for this source.

## Scale verdict

The original legacy/segmented cases remain mandatory. Segmented thread and process variants add the same 0/1,000/10,000 histories, production watchdog, heartbeat and successful final-send requirement. Numeric latency budgets and instrumentation headroom are unchanged. All new capacities are explicitly synthetic: 20,000 orders, 40,000 trades and a 64 MiB snapshot bound. Thread policy allows 2 seconds; process policy allows 15 seconds for explicit cold startup. Actual refresh jobs are still capped at the configured **1-second** joint deadline. No larger startup budget changes that deadline.

The isolated run reports **7 passed, 6 failed**, with six existing warnings. Both 10k baseline formats breach latency ceilings; both worker 1k cases breach the isolated event-loop ceiling. Both worker 10k cases time out during reconciliation, before final send: they are failures, not faster sends or accepted risk denials. Observed milliseconds:

| Format/backend | Completed intents/fills | Joint poll | Propose → final send | Maximum loop lag | Cancel start |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy | 0 | 4.5 | 48.2 | 52.7 | 14.5 |
| Legacy | 1,000 | 114.7 | 279.8 | 288.9 | 101.2 |
| Legacy | 10,000 | 1369.3 | 2965.6 | 3064.4 | 597.1 |
| Segmented | 0 | 7.9 | 125.3 | 142.9 | 18.9 |
| Segmented | 1,000 | 90.4 | 301.9 | 313.8 | 46.0 |
| Segmented | 10,000 | 1597.4 | 3122.2 | 3318.7 | 319.6 |
| Segmented/thread | 0 | 7.9 | 70.8 | 78.3 | 10.3 |
| Segmented/thread | 1,000 | 190.6 | 316.4 | 855.3 | 45.6 |
| Segmented/thread | 10,000 | timeout | not reached | incomplete | not reached |
| Segmented/process | 0 | 21.5 | 111.7 | 118.7 | 10.2 |
| Segmented/process | 1,000 | 1051.6 | 315.6 | 864.9 | 45.0 |
| Segmented/process | 10,000 | timeout | not reached | incomplete | not reached |

These measurements do not demonstrate an overall latency improvement. Source capture, serialization/IPC, exact integrity reads, cross-journal replay and monolithic checkpoint/send work still depend on total history. The worker mechanism permits cancellation to be scheduled during its own work, but cannot bound synchronous work elsewhere.

## Remaining delivery

1. Implement coordinated bounded active/archive state for every financial/ownership journal, including pending-intent future-settlement capacity. Capital/joint segments alone are insufficient.
2. Make snapshot capture, IPC and financial/provenance qualification incremental over bounded active state. Retain exact corruption detection, deduplication evidence, clock/writer fences and WAL/fsync ordering.
3. Remove repeated historical decode/replay on proposal/filter/final send and safety/cancel paths. Profile cache retention and garbage collection before assigning them a causal share of the latency; neither is established as the sole cause by this checkpoint.
4. Pass every unchanged isolated scale case and then the full completion audit and both pinned coverage gates. No worker timeout, warmed history outside the measured cycle, budget increase or hidden skip can substitute for an affordable successful 10k-history send.
