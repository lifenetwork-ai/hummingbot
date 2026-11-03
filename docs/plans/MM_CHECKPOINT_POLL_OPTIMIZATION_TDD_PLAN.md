# Checkpoint and background-poll optimization

Scope: reuse the recorder, owner fencing and existing PostgreSQL schema. Preserve
terminal-fill proof, Decimal precision, HALT and recovery behavior. No authority
cache, new service or Redis dependency.

## P1 — Position checkpoint batch

- [x] Red: require one lookup/transaction for multiple position snapshots; test
  exact values, repeated keys, rollback, stale ownership and retained in-memory
  exposure after failure.
- [x] Batch recorder writes and submit the complete orchestrator snapshot once.
  Keep connector reads and ownership-connection access on the engine loop.
- [x] Green: native recorder/executor/terminal-proof and real PostgreSQL tests.
- [x] Record evidence and commit. Batching does not claim to remove all synchronous
  recorder IO or explain historical probe latency.

## P2 — Idle reconciliation polling

- [x] Red: an empty queue must not lock bot_state; verify completed/stale candidates,
  owner changes and concurrent claims cannot weaken the existing transaction.
- [x] Add a non-locking candidate probe; recheck under the existing owner/control
  lock before claiming work, keeping the current lock order.
- [x] Green: PostgreSQL concurrency/ownership tests and API/service regressions.
- [x] Record evidence and commit.

## P3 — Funding discovery measurement

- [x] Measure the existing history aggregate with EXPLAIN ANALYZE on a disposable
  large PostgreSQL dataset; preserve raw evidence under build/.
- [x] Change the query only if measured cost warrants it; preserve historical
  pair discovery and earliest timestamps, including newly imported old orders.
- [x] Run relevant regressions, document limitations and commit results.

## Completion

- [x] Run the combined relevant native/PostgreSQL checks, update progress and
  publish an optimization report. New source does not inherit prior image/load/
  Demo acceptance; do not trade, repair Demo data or reset HALT for this task.

Completed source checks: 2,357 native tests plus 11 subtests, 307 PostgreSQL
tests and Swagger pass. [Report/evidence](../../reports/performance/mm-checkpoint-poll-optimization.md).
No new image/load/Demo gate was requested or claimed for this optimization.
