# Reporting admission and startup optimization

Date: 2026-11-02 (Asia/Saigon).

Order requested by the operator: daily admission, measured historical startup,
then reporting SQL review. Run load verification last on one immutable image.
Reuse existing workers, PostgreSQL, recorder, Decimal arithmetic and caches.
Persistent Demo storage/configuration and exchange accounts are untouched.

- [x] A: Red/Green independent uncached daily filters, same-key coalescing,
  cancellation, bounded outstanding keys, shutdown, TTL0 and day rollover.
  Preserve worker/queue/control priority and explicitly stale fallback rules.
- [x] A: Run focused and broad API regressions, document the concurrency contract
  and commit the completed phase.
- [x] B: Measure startup on realistic 20k/100k/1M executor history, selected scope,
  query count and Python peak memory with disposable PostgreSQL. If allocation
  is material, remove unnecessary row hydration with differential/cost tests;
  preserve exact Float-to-Decimal behavior and cursor cleanup. Commit findings.
- [x] C: Reassess existing daily SQL measurements and current 100k/1M behavior.
  Retain a change only with exact-result equality and repeatable improvement;
  otherwise retain bounded reporting and document the scaling decision. Commit.
- [x] Final: Freeze application/measurement source, package the final runtime,
  run native/PostgreSQL/Swagger and targeted image regressions, then unchanged
  full R1 (including60-minute soak and recovery cases) and matched shipping R2.
- [x] Publish exact results, preserve failures/remaining attribution limits,
  update progress/checklists and commit. No historical pass certifies a new image.

Healthy latency/error/resource/retention thresholds and order recovery remain
unchanged. Do not introduce Redis, an incremental ledger or weaker authority
checks without evidence. Short measurements do not replace the final hour gate.

Completed on image `39198499`, source `9db4ff56a`: native2,265 plus11 subtests,
PostgreSQL282, Swagger, three same-digest E2E groups, full unchanged R1 and
matched shipping R2 pass. See [report](../../reports/performance/mm-reporting-startup-optimization.md)
and [evidence](../../reports/performance/mm-reporting-startup-evidence.json).
R1 probe episodes and incomplete attribution remain explicit; completing this
plan does not assert zero transient stalls or a new connected Demo acceptance.
