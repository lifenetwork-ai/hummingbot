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

- [x] Investigate the three groups of seven unpaused empty quote samples using
  guard/admission/executor evidence. The historical final group includes a
  liquidity guard; scheduled refresh is directly captured in the later baseline.
  Red/Green and matched terminal-order joins confirm the avoidable five-second
  shutdown wait. Historical executor spans were absent; do not invent old IDs.
- [ ] Declare a production quote-coverage SLO separating normal replacement
  gaps from permitted protective pauses before claiming uninterrupted two-sided
  coverage. Guard policy is unchanged; local improvement does not certify this SLO.

## Quote replacement follow-up — 2026-11-02

- [x] Review retained venue/order/guard records and existing executor lifecycle.
  A fixed five-second cancellation wait is an avoidable delay after terminal
  callbacks; the final cluster also includes a protective liquidity qualification stop.
- [x] Red/Green callback-driven completion of the existing shutdown wait. Keep
  the original five-second no-ACK retry deadline, require all tracked orders to
  be terminal, preserve held partial fills and drain wait tasks on cancellation.
- [x] Retain bounded whole-probe executor/stop-proposal timing so replacement
  intervals can be inspected even when no health-probe incident occurs.
- [x] Run appropriate native/PostgreSQL checks and relevant packaged E2E;
  compare unchanged-baseline/candidate bounded diagnostics with matching fixtures.
- [x] Publish measured attribution, remaining guard/pipeline gaps and artifact
  scope; clean owned resources and commit. Never remove a protective stop to
  satisfy quote-count targets or inherit a prior artifact's release acceptance.

The structural acceptance is removal of fixed idle waiting once existing terminal
state is known, not a global guarantee of uninterrupted quoting. A production
coverage SLO remains dependent on the approved strategy and protective-stop policy.

[Result/evidence](../../reports/investigations/mm-quote-recovery.md): native2,356
plus11 subtests, PG296, Swagger and three packaged E2E groups pass. Matched180s
diagnostics on4ef2c50a/660bd38b pass: median post-terminal wait4,993→99ms;
longest empty-point span6.023→2.006s,180 synthetic fills reconcile per run.
Guard/control-cycle gaps remain. Candidate has no new full R1/R2/hour/Demo gate.

## Quote-recovery image full load and connected Demo — requested 2026-11-02

Pin `660bd38b` by manifest and freeze measurement/runtime sources. Retain every
failed attempt; keep the existing durations, resources, error and latency budgets.

- [x] Complete full R1: warm300/mixed900/burst90/Random-soak3600 and six fault groups.
- [x] Independently verify R1:58 continuous minute samples, maximum gap65.75s,
  unchanged source/artifact, all three profiles and4,885 reconciled synthetic fills.
- [ ] Complete matched ordinary-entrypoint R2 warm300/mixed900 and verify it independently.
- [x] Current-image Demo read-only REST/public-private WS/account/instrument review;
  read recovery from a read-only copy of the stopped original volume. No pending
  orders/HALT/mutations; stored perpetual quantities match the venue.
- [x] Back up the existing Demo database privately, restore the absent local service
  on this image and verify stopped startup without changing private trading config.
- [x] Run three profiles on spot/perpetual/both, real600s Random rollover, active
  restart and Stop; independently verify actual Demo orders and terminal cancellation.
- [x] Reconcile new fills/original fees/positions and repeated reads; preserve historical
  fills. Zero new fills cannot count as positive-fill acceptance. Leave trading stopped.
- [ ] Positive new-fill/original-fee processing on this image remains unverified:
  the controlled Demo protocol has zero new fills,407 canceled orders. Historical
  six fills and simulator fills do not close this connected positive-fill item.
  After qualified load/restoration, reuse the previously approved temporary40USDT
  per-market, one-level/0.0003-base-spread preset for a bounded1200s natural-maker
  observation and filled restart; retain fee/volatility/risk guards and restore
  the unchanged canonical catalogue. No forced fills or automatic HALT reset.
- [ ] Publish scoped evidence, update acceptance/progress, clean temporary resources
  and commit. No live trading, HALT reset, database repair or Cloud Run deployment.

Raw load progress: `build/quote-recovery-release-20261102/`;
connected evidence: `build/quote-recovery-demo-20261102/`.

[Connected Demo protocol](../../reports/demo/okx-demo-quote-recovery.md) passes
all nine fixed cases,600s Random rollover, active spot/both restart and independent
407-order terminal review. Random draws sideways again; no different-profile
handover or new fill is claimed. First full R1 attempt fails burst read p99
1.153s/auxiliary1.126s; warm/mixed pass, no unexpected API errors and1,290
synthetic fills reconcile. Retain that failure; unchanged retry runs with Demo
containers temporarily stopped. R1 now passes completely: soak p99103ms, no
unexpected API errors,14.18MiB retained growth against64MiB,58 continuous samples
and all six fault groups. R2 is still running; full combined qualification and
the positive-fill observation remain open until their own checks finish.
