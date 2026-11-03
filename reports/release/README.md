# Release verification

[All reports](../README.md) · [Latest local load gates](mm-okx-expiry-load.md)

The [expiry-fix load report](mm-okx-expiry-load.md) covers image `7dca3ce9`,
including the interrupted/overlapping controls and clean repeat measurements.
Its connected Demo acceptance remains open. The earlier
[quote-recovery release](mm-quote-recovery-release.md) qualifies660bd38b
with full R1 and matched R2; its connected Demo follow-up includes five natural
maker fills, exact fees and filled restart. First failed burst and recovered
probe episodes remain explicit. No live account or Cloud Run deployment.

Start with the latest local load report above. Earlier image-specific attempts retain their
original failures and scope; they are not additional open release gates.

| Report | Evidence and scope |
| --- | --- |
| [Expiry-fix offline load](mm-okx-expiry-load.md), [evidence](mm-okx-expiry-load-evidence.json) | Image `7dca3ce9`: full R1, separately repeated warmup/mixed and clean R2; the original warmup overlapped a clone dry run. Demo remains open. |
| [Checkpoint-poll offline load](mm-checkpoint-poll-load.md), [evidence](mm-checkpoint-poll-load-evidence.json) | Earlier image `e9845e3f`: full R1/R2 passed; its Demo failed at Random rollover. |
| [Quote-recovery release](mm-quote-recovery-release.md), [evidence](mm-quote-recovery-release-evidence.json) | Image660bd38b full R1/R2 pass: soak p99103ms, +14.18MiB retained,4,885 synthetic fills, six faults, shipping mixed64ms. Actual Demo five new maker fills/fees and filled restart pass separately. First failure/residual probes retained; canonical trading stopped. |
| [Runtime logic load rerun](mm-runtime-logic-release.md), [evidence](mm-runtime-logic-release-evidence.json) | Image95cde31c full R1/R2 pass: soak p99 465ms, retention +12.69MiB, 4,885 matched fills/positions, all three Random profiles and six faults; shipping mixed p99 65ms. Probe recoveries R1 38 engine/two API, R2 two engine; no new connected Demo. |
| [Status-publication release](../investigations/mm-engine-status-publication.md), [evidence](../investigations/mm-engine-status-publication-evidence.json) | Image e2d393b1: source regressions/three E2E groups, full R1 and matched shipping R2 pass; soak p99 113ms, retention +13.18MiB and4,886 matched fills/positions. Residual probes15/6 recover without restart; causal limits remain. No new connected Demo upgrade. |
| [Reporting/startup release](../performance/mm-reporting-startup-optimization.md), [evidence](../performance/mm-reporting-startup-evidence.json) | Image39198499: native/PG/Swagger/three E2E groups, full R1 and matched shipping R2 pass; soak p99 553ms, zero errors, +14.03MiB retained and4,886 matched fills. R1 recovered probes and historical causal limits remain explicit; no new connected Demo upgrade. |
| [Submission-log release](mm-submission-log-release.md), [evidence](mm-submission-log-release-evidence.json) | Image a190b024: packaged smoke/targeted E2E, full R1 and matched R2 pass; soak p99 643ms, zero API errors, retention +14.39MiB and4,885 matched fills. Stopped Demo upgrade preserves config/history. Earlier causal gaps retained. |
| [Prior-image closeout](mm-release-closeout.md), [evidence](mm-release-closeout-evidence.json) | Image5973410d full R1/R2 passes;4,884 fills match, soak p99 717ms and zero errors. Includes separately scoped3f5cbd0f follow-up; earlier failures and unresolved causes preserved. |
| [Runtime supervision](mm-service-supervision.md) | Local acceptance complete: final image/regression/E2E/lifecycle, hour/load/faults and matched ordinary-entrypoint control pass; prior failures retained |
| [Fixed-image full release](e526-release.md) | [Evidence](e526-release-evidence.json); strict full release gates failed; bounded reporting-isolation diagnostics passed |
| [Packaged supervisor-diagnostics release](probe-release-20261030.md) | Image dd602fb2: E2E/recovery/ordinary control pass; Random soak fails fill-producer drain, despite passing API latency |
