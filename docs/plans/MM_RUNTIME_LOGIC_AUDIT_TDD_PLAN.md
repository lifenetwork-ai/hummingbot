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

## Trading-continuity diagnostics — requested 2026-11-02

Process liveness and a passing API/load gate do not prove continuous MM work.
Add opt-in, bounded test instrumentation using the existing simulator, incident
recorder and packaged service. Keep production decisions and health budgets intact.

- [x] Red/Green strategy tick/controller-cycle start gaps and execution durations;
  retain instance identity, readiness and errors without retaining runners.
- [x] Red/Green connector submit/cancel call-to-return timing, distinguishing
  exceptions/cancellation/false returns from acknowledgments or terminal venue state.
- [x] Independently sample simulator live-order age and terminal cancel observations
  while the engine cannot advance; bound live tracking, history, disk and loss counts.
- [x] Correlate those observations with probe incidents, expose missing/evicted
  evidence and sampling gaps; old runs must report not instrumented, never zero downtime.
- [x] Verify opt-in packaging plus a bounded offline diagnostic with verified
  packaged application bytes, publish evidence/limits, clean owned resources and
  commit. The prior image disappeared from Docker before measurement; a rebuilt
  artifact must retain its own identity and cannot inherit that image's gates. Injected pauses
  are classified separately and never promoted to a healthy load acceptance.

Order age means time resting at the simulator, not proof of stale pricing. Tick
completion does not prove quote admission. Connector RTT does not prove exchange
terminal cancellation. No new production API/env, Demo session or deployment is
part of this diagnostic phase; actual OKX/network latency remains unverified.

[Result/evidence](../../reports/investigations/mm-trading-continuity.md):946 CLI
tests pass. New diagnostic image `4ef2c50a` matches 1,155 reviewed runtime/config
files; it does not inherit the deleted `95cde31c` artifact's release gates.
Unpaused180s and separately classified90s injection diagnostics complete. The
confirmed2.272s engine pause produces tick/controller gaps and independent live
quote-age samples while retaining the same engine PID. Old runs remain uninstrumented.

### Operational follow-up revealed by the new measurements

- [ ] Correlate the three groups of seven unpaused empty quote samples with
  existing guard/admission/executor reasons; declare acceptable replacement and
  protective-stop gaps before claiming uninterrupted two-sided quote coverage.
  This is not a confirmed runtime defect or closed production-availability gate.
