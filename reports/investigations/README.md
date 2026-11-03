# Incident investigations

[All reports](../README.md) · [Current release](../release/mm-checkpoint-final-release.md)

The latest [quote-wait diagnostics](mm-quote-wait-diagnostics.md) expose per-side
local evidence, stage duration and operator guidance while preserving unknown
causes and existing trading authority. Native2,429 +11 subtests/focused44/PG19/
Swagger pass; no new artifact/load/Demo acceptance is claimed.

The preceding [guard/recovery coverage review](mm-guard-recovery-coverage.md) adds36
native/PG cases for withdrawal, explicit Stop authority and post-throttle safety.
Related native541 +2 subtests, full PG329 and the focused PG10 module pass;
overlapping scopes and94 expected mutation failures are explicit. Production
code and the accepted image remain unchanged.

The preceding [final-image probe/MM correlation](mm-checkpoint-probe-correlation.md)
reviews all21 retained historical R1 episodes and passes a new600s diagnostic
on `51d4d879`. Both new probe episodes retain trading progress; no new runtime
defect or threshold change is justified. Historical MM timing remains unavailable.

The earlier [quote-replacement follow-up](mm-quote-recovery.md) fixes the idle
five-second wait after terminal confirmation. Broad source checks, three
packaged E2E groups and matched180s diagnostics pass; median post-terminal wait
falls4,993→99ms. Protective gaps and production quote-coverage policy remain;
the subsequent [full R1/R2 and connected Demo follow-up](../release/mm-quote-recovery-release.md)
qualifies660bd38b under its stated scope.

The preceding [trading-continuity diagnostic](mm-trading-continuity.md) adds MM
cycle/order timings and independent venue samples. It validates detection of a
controlled engine pause; its empty-sample investigation is followed up above.
The rebuilt diagnostic artifact does not inherit older release gates.

The earlier logic audit fixes durable-HALT cleanup and equivalent-fee replay,
with broad source and targeted image recovery checks. The subsequent
[same-image load rerun](../release/mm-runtime-logic-release.md) passes full R1
and matched R2 on `95cde31c`; no new connected Demo acceptance is claimed.
The preceding residual-probe follow-up removes a duplicate observer read and
completes targeted candidate validation; full release scope remains below.
The status-publication follow-up confirms and fixes one blocking path, with
full local image gates complete. The controlled comparison is the preceding
checkpoint; residual probe and historical causes keep their attribution limits.
Older reports preserve their diagnostic checkpoints and failed runs;
current release acceptance is recorded separately in the release closeout.

| Report | Evidence and scope |
| --- | --- |
| [Quote-wait diagnostics](mm-quote-wait-diagnostics.md), [evidence](mm-quote-wait-diagnostics-evidence.json) | TDD, native2,429 +11 subtests/focused44/real-PG19/Swagger pass. Optional API/log wait evidence; no trading rule change or new image/load/Demo acceptance. |
| [Guard/recovery coverage](mm-guard-recovery-coverage.md), [evidence](mm-guard-recovery-coverage-evidence.json) |36 added cases; native541 +2 subtests/full PG329/focused PG10 pass with stated overlap. Five mutants yield94 expected failures; no production change or new runtime acceptance. |
| [Final-image probe/MM correlation](mm-checkpoint-probe-correlation.md), [evidence](mm-checkpoint-probe-correlation-evidence.json) | Same-image600s passes, p99 71.41ms/600 matched synthetic fills. Two probe episodes retain tick/controller/submission progress; no runtime patch or threshold change, historical MM timing unavailable. |
| [Quote replacement](mm-quote-recovery.md), [evidence](mm-quote-recovery-evidence.json) | Fixed terminal-confirmed idle wait; native2,356 plus11 subtests/PG296/Swagger, three image E2E groups and matched180s diagnostics pass. Median4,993→99ms; protective gaps remain, no new full-hour/Demo gate. |
| [MM trading continuity](mm-trading-continuity.md), [evidence](mm-trading-continuity-evidence.json) |946 CLI tests,180s offline probe and a separate2.272s engine-pause diagnostic. Independent samples persist while engine pauses; empty quote clusters remain an explicit follow-up. New4ef2c50a is diagnostic only. |
| [Runtime logic audit](mm-runtime-logic-audit.md), [evidence](mm-runtime-logic-audit-evidence.json) | Two confirmed runtime defects fixed with Red/Green; native2,310 plus11 subtests/PG296/Swagger, two targeted E2E groups and external HALT/restart pass on95cde31c. No new full-release/load/Demo claim. |
| [Further residual probes](mm-engine-residual-probes.md), [evidence](mm-engine-residual-probes-evidence.json) | Observer queries3→2, real HTTP Red/Green, native/PG and MM lifecycle pass. Candidate18422eca passes300s diagnostic; no full-release or all-probe resolution claim, executor runtime unchanged. |
| [Status-publication responsiveness](mm-engine-status-publication.md) | Real DB/HTTP legacy Red and candidate Green; final native/PG, rebuilt image and three E2E groups pass. Same-base 600s probes are 2 vs 2; full R1/R2 pass on e2d393b1 with residual probes15/6. Ownership/executor/recorder leads remain, unique cause open. |
| [Controlled engine-probe comparison](mm-engine-probe-controlled.md), [evidence](mm-engine-probe-controlled-evidence.json) | All54 prior episodes reviewed; matched600s ordinary/scalar/rich controls pass with1/1/4 recovered probes and no API errors. Current-image read-only Demo preflight passes; no new shipping fix or unique historical cause claimed. |
| [Engine probe diagnostics](mm-engine-probe-latency.md), [evidence](mm-engine-probe-latency-evidence.json) | Longest-span retention, reserved incident capacity and cheaper hot-leaf instrumentation;61 tests, smoke and300s probe pass. One transient probe recovers; no unique runtime cause or new full release is claimed. |
| [Focused historical-cause review](mm-historical-cause-review.md), [evidence](mm-historical-cause-evidence.json) | Test CPU/heartbeat timestamp defect reproduced and fixed;119 tests and packaged old/new comparison pass. Old H1/H2/H3 causes remain unproven; accepted R1/R2 unchanged. |
| [Measurement follow-up](mm-measurement-remediation.md), [evidence](mm-measurement-remediation-evidence.json) | On-loop observer cost corrected; measured disk-cache retention contract declared before new gate. Native114, bounded probes, full R1 and matched shipping R2 pass. |
| [Automatic incident capture](mm-incident-investigation.md), [evidence](mm-incident-investigation-evidence.json) | 61 related tests and matched bounded diagnostics pass; full release is subsequently accepted in current closeout, historical long-lag cause remains unproven |
| [Single-service process isolation](mm-process-isolation-experiment.md), [evidence](mm-process-isolation-evidence.json) | Historical experiment and retained failures; later runtime adoption/acceptance is in the runtime supervision report |
| [Daily reporting latency](mm-reporting-latency.md), [evidence](mm-reporting-latency-evidence.json) | SQL scaling unresolved; fallback verified; later final architecture/load acceptance is complete in the runtime supervision report |
| [P99 investigation](mm-p99-investigation.md), [evidence](mm-p99-investigation-evidence.json) | Reproduced traced/untraced tails, confirmed daily SQL timeout, production-entrypoint control and remaining attribution limits |
| [Short diagnostic iteration](life-mm-short-repro-review.md) | [CPU/probe evidence](life-mm-short-repro-evidence.json); datetime-only serialization and nested UTC fix; historical short-iteration results; follow-up closed after passing full gate |
| [Extended traced load review](life-mm-trace-load-review.md) | [Portable trace evidence](life-mm-trace-load-evidence.json); mixed passed, sixty-minute soak failed p99/timeouts |
| [Remaining burst/supervisor follow-up](mm-burst-followup.md) | [Evidence](mm-burst-followup-evidence.json); historical failed burst and verified client fix retained; later R1/R2 pass, original causal attribution remains limited |
| [Engine liveness investigation](mm-engine-liveness-investigation.md) | [Evidence](mm-engine-liveness-investigation-evidence.json); probe diagnostics and retained incident timeline; original sustained-failure cause remains open |
| [Random handover investigation](mm-random-handover.md) | [Evidence](mm-random-handover-evidence.json); three verified bugs closed; burst-rate and historical liveness investigations remain open; combined full run failed burst rate |
