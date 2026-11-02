# Engine status-publication responsiveness

Date: 2026-11-02 (Asia/Saigon). Continue the
[controlled probe investigation](MM_ENGINE_PROBE_LATENCY_TDD_PLAN.md).
Baseline accepted image `39198499`, application source `9db4ff56a`.

- [x] Inspect all retained stacks and isolate an avoidable synchronous status
  publication on the engine loop. Preserve limits on historical attribution.
- [x] Red/Green loop responsiveness during slow writes, detached snapshots,
  one physical publication, cancellation/drain, failed commits, queued shutdown,
  completion/cancellation races and unchanged daily checkpoint cadence.
- [x] Reproduce an actual 300ms private HTTP probe timeout on the legacy host
  with a 400ms PostgreSQL publication wait. Verify the same probe on the candidate
  and real DB rejection of a superseded control version.
- [x] Run broad native/PostgreSQL/Swagger regressions; commit the runtime phase.
  Final source: 2,279 native tests plus 11 subtests, 284 PostgreSQL tests and
  Swagger pass.
- [x] Rebuild the exact source using the Dockerfile (previous image was externally
  removed), verify hashes/imports,
  run targeted E2E and a matched 600s ordinary-entrypoint diagnostic. Compare
  against the same-base legacy counterfactual, without claiming all probe
  causes are fixed. Three targeted E2E groups and both 600s controls pass;
  engine probe recoveries remain 2 vs 2, with zero API errors in both.
- [x] Freeze the final source and run unchanged full R1 plus matched shipping R2
  after shorter checks pass. Preserve every failure and original gate budgets.
- [x] Publish scoped report/evidence, update current status/checklists, commit
  and clean only owned test resources. Demo volume/config/account stay untouched.

Snapshot and cadence decisions remain on the event loop. Reuse the existing
thread executor and SQL store with at most one physical publication; a canceled
waiter never detaches shutdown from a still-running write. New publication is
closed before final cleanup; drain/error handling precedes final status and
ownership release. SQL fencing, control/receipt transaction, fill persistence,
probe timeout/grace, DB pool/IO limits and trading/risk authority stay unchanged.
This addresses one confirmed blocking path, not every synchronous recorder call.

Final local outcome: `e2d393b1`, source `a67233dc9`, full unchanged R1 and
matched shipping R2 independently pass. Soak p99 112.71ms, retention +13.18MiB,
4,886 reconciled fills/positions and all six faults pass; R2 mixed p99 71.80ms
with zero API errors. Probe recoveries remain 15 in R1 / 6 in R2; the specific
publication wait is fixed, unique attribution of all transient misses stays
open. [Report/evidence](../../reports/investigations/mm-engine-status-publication.md).
No new-image connected Demo or Cloud Run deployment is claimed.
