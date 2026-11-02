# Release verification

[All reports](../README.md) · [Latest local release](../investigations/mm-engine-status-publication.md)

**New confirmed defects after the historical gates:** [runtime logic audit](../investigations/mm-runtime-logic-audit.md)
fixes durable-HALT cleanup and equivalent-fee replay. Candidate `95cde31c` passes
broad source and targeted image checks; its full release/load/soak gate has not
been rerun. The full-gate images below predate these fixes.

Start with the current closeout. Earlier image-specific attempts retain their
original failures and scope; they are not additional open release gates.

| Report | Evidence and scope |
| --- | --- |
| [Status-publication release](../investigations/mm-engine-status-publication.md), [evidence](../investigations/mm-engine-status-publication-evidence.json) | Image e2d393b1: source regressions/three E2E groups, full R1 and matched shipping R2 pass; soak p99 113ms, retention +13.18MiB and4,886 matched fills/positions. Residual probes15/6 recover without restart; causal limits remain. No new connected Demo upgrade. |
| [Reporting/startup release](../performance/mm-reporting-startup-optimization.md), [evidence](../performance/mm-reporting-startup-evidence.json) | Image39198499: native/PG/Swagger/three E2E groups, full R1 and matched shipping R2 pass; soak p99 553ms, zero errors, +14.03MiB retained and4,886 matched fills. R1 recovered probes and historical causal limits remain explicit; no new connected Demo upgrade. |
| [Submission-log release](mm-submission-log-release.md), [evidence](mm-submission-log-release-evidence.json) | Image a190b024: packaged smoke/targeted E2E, full R1 and matched R2 pass; soak p99 643ms, zero API errors, retention +14.39MiB and4,885 matched fills. Stopped Demo upgrade preserves config/history. Earlier causal gaps retained. |
| [Prior-image closeout](mm-release-closeout.md), [evidence](mm-release-closeout-evidence.json) | Image5973410d full R1/R2 passes;4,884 fills match, soak p99 717ms and zero errors. Includes separately scoped3f5cbd0f follow-up; earlier failures and unresolved causes preserved. |
| [Runtime supervision](mm-service-supervision.md) | Local acceptance complete: final image/regression/E2E/lifecycle, hour/load/faults and matched ordinary-entrypoint control pass; prior failures retained |
| [Fixed-image full release](e526-release.md) | [Evidence](e526-release-evidence.json); strict full release gates failed; bounded reporting-isolation diagnostics passed |
| [Packaged supervisor-diagnostics release](probe-release-20261030.md) | Image dd602fb2: E2E/recovery/ordinary control pass; Random soak fails fill-producer drain, despite passing API latency |
