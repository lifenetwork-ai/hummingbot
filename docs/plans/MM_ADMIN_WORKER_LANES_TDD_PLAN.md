# Admin database worker lanes — TDD plan

2026-11-04 (Asia/Saigon).

Protect operational reads and session controls from queueing behind daily
aggregates and history queries. Reuse the existing bounded executor and two
physical workers; keep SQL, database connection limits, deadlines, cache policy,
API payloads and the trading engine unchanged.

## Scheduling contract

- One operations job and one heavy job may run concurrently. Operations include
  current status, balances, positions, performance, liquidity, stored check
  receipts and session commands. Controls retain priority over queued operational
  reads. Running calls are not preempted.
- Daily statistics and history pages/timelines over100 rows use the heavy lane.
  Existing default-sized pages (up to100) and pinned configuration reads remain
  operational. FIFO admission gives accepted heavy jobs progress independent
  of a continuous stream of operations/controls.
- Keep total pending32 and non-control pending12. Cap heavy pending at10 so two
  non-control admission slots remain available to operations. Disconnect/cancellation cannot
  release a running physical call. Reject overload safely; shutdown cancels queued
  work and drains physical workers. A test-only one-worker configuration retains
  existing control-first serial behavior.
- Preserve context propagation and test diagnostics, including workload labels.
  No new service, thread-pool library, environment switch or cache is required.

## Checklist

- [x] Demonstrate Red: a blocked daily job plus queued large history must not occupy
  both workers or delay a balance read; exercise actual API handlers.
- [x] Implement lane-aware scheduling and explicit handler routing; test control
  priority, heavy FIFO progress, cancellation, exceptions, overload and shutdown.
- [x] Add Red/Green reserved admission under a heavy queue flood. Reject the
  intermediate all-history classification: a separate Red test shows a small
  page unnecessarily waits behind an aggregate. Keep default-sized pages
  operational and test both sides of the100/500-row boundary.
- [x] Update pre-existing concurrency tests to hold one job from each lane,
  preserving physical-capacity assertions. TTL0 still performs distinct reads;
  those heavy reads now run serially.
- [x] Verify real PostgreSQL capacity and HTTP Stop/receipt availability while
  history is running. Run related and broad native/PG regression checks.
- [x] Commit source `0f4095671`; rebuild image086454de and verify ten runtime
  hashes and197 unchanged Python packages. Run
  packaged E2E and controlled slow-report recovery/fallback tests.
- [x] Compare the unchanged300s load against the retained previous image result,
  preserving all outcomes. Run the unchanged standalone hour on the new image;
  report per-route latency, errors/stale, ledger and retention honestly.
- [x] Verify stopped Demo/configuration/ledger restoration; update the current
  acceptance register and publish scoped evidence. Commit verification results.

No result proves uninterrupted quoting or exclusive causality of the historical
p99 spike. A new lane policy must not inherit an older image's release acceptance.
The measured hour passes, but preparation includes one2s owner-state FOR UPDATE
timeout and automatic engine restart. Its blocker/trigger is not captured;
the report preserves this observation without claiming a restart-free startup.
