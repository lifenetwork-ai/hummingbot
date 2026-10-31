# Incident investigations

[All reports](../README.md) · [Current release](../release/mm-release-closeout.md)

The focused historical-cause review is the latest attribution summary.
Older reports preserve their diagnostic checkpoints and failed runs;
current release acceptance is recorded separately in the release closeout.

| Report | Evidence and scope |
| --- | --- |
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
