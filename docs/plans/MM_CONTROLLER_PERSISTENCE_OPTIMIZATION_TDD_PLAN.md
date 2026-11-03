# Controller and persistence optimization

Date: 2026-11-03. Keep existing upstream controller/executor behavior and all
durable ownership/send fences; introduce no risk-data TTL cache or new service.

- [x] P1 Red/Green: compute exposure once per synchronous controller decision,
  including waiting-for-report protection. Public admission/readiness checks and
  later decisions must always read fresh data, including across queue awaits.
- [x] Reject a negative spot account observation before positive live BUY
  reservations can hide it; verify both protective cancellation and the real-PG
  post-throttle send gate. This inherited defect was exposed by P1 boundary tests.
- [x] Verify controller/lifecycle/guard regressions, record counts and commit P1.
- [x] P2 Measure real PostgreSQL intent/tracking query counts, snapshot work and
  cohort cost before choosing a change. Preserve commit-before-send and unknown
  intent detection; optimize only a measured redundant operation.
- [x] Red/Green any P2 change with real PG, including queued/unadmitted creates,
  missing-but-pending tracking, market/config isolation and failed durability.
- [x] P3 Profile repeated readiness math; retain fresh guards and avoid speculative
  cache/architecture changes when no measured benefit is established.
- [x] Run combined required checks, publish scoped results, update checklists and
  commit. Source verification does not inherit older image/load/Demo acceptance.

Completed 2026-11-04. Native2,442 +11 subtests; PG342 initial passes plus the
corrected SQL-budget case in an 11-case focused rerun (343 distinct cases covered,
not a single 343-pass run). Swagger/style pass. Detailed scope, mixed latency
samples and unchanged image qualification are in the
[report](../../reports/performance/mm-controller-persistence-optimization.md).
