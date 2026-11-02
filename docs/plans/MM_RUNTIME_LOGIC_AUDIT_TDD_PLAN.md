# Runtime logic audit and regression fixes

Date: 2026-11-02 (Asia/Saigon). Follow the completed
[residual probe investigation](MM_ENGINE_RESIDUAL_PROBE_INVESTIGATION.md).

- [x] Review ownership/control/session, shutdown, fill replay and optional
  background-observer boundaries. Report confirmed defects separately from risks.
- [x] Red/Green external durable HALT while core/startup remains active. Cleanup
  resting quotes/startup before a halted snapshot; failed cleanup must require
  process recovery rather than publish success. Preserve existing stop/recovery.
- [x] Red/Green equivalent duplicate fill fees with different Decimal text.
  Reuse existing fee models, preserve the first raw payload, reject real fee
  changes and never acknowledge a failed commit.
- [x] Run broad native/PostgreSQL checks and relevant image lifecycle/recovery
  tests; record actual scope without borrowing older artifact acceptance.
- [x] Publish findings/evidence, update checklists/progress and commit phases;
  clean owned resources. Do not mutate Demo/account/config or deploy.

[Result and scope](../../reports/investigations/mm-runtime-logic-audit.md):
2,310 native tests plus 11 subtests, 296 PostgreSQL tests, Swagger, two targeted
E2E groups and packaged external HALT/restart pass. Those audit checks alone
exclude full load/soak and connected Demo acceptance; the later load rerun is below.

## Final-image load rerun — requested 2026-11-02

Use the same `95cde31c` image, unchanged source/fixtures and acceptance budgets;
test only disposable local PostgreSQL and exchange simulator resources.

- [x] Complete and independently verify full R1: warmup 300s, mixed 900s,
  burst 90s, Random soak 3,600s and all six injected fault groups.
- [x] Complete and independently verify matched ordinary-production-entrypoint
  R2: warmup 300s and mixed 900s on the same image and effective fixture.
- [x] Publish results and retained failures, update acceptance pointers, clean
  owned resources and commit. No connected Demo or Cloud Run deployment.

Raw progress: `build/logic-audit-load-20261102/`.
[Result/evidence](../../reports/release/mm-runtime-logic-release.md): full R1
and matched R2 pass on the same image. Soak p99 465ms, zero unexpected API
errors, +12.69MiB retained, 4,885 matched fills/positions and all six faults.
R2 mixed p99 65ms. Recovered probes remain explicit: R1 38 engine/two API,
R2 two engine; no unique cause or connected Demo/deployment claim.
