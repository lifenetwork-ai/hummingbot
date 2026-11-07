# Incident and code investigations

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

Current conclusions are in the [runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [current image report](../release/mm-runtime-followup-load.md). Five soak
engine probes, one mixed API probe and one engine probe between windows recover;
their intervals are supervisor observations, not quote downtime. Mixed/burst and
probe counts improve versus `837e7997…`, but warmup/soak latency and retention
regress within budgets; no uniform speedup is claimed. The previous image's first
shutdown poll timeout remains unexplained and retained in Git `ff6b85ded` despite
its passing unchanged retry. The new image passes all targeted E2E first attempts.
No unique historical probe/p99/publication trigger is established; bounded captures
cannot reconstruct unrecorded MM activity.

The [ownership-read investigation](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces a read-COMMIT probe delay and tests a scoped driver change. Its first
isolation prototype is rejected for unbounded pool-reset callbacks. Source native/
PG checks and the subsequent unchanged image hour gate pass separately. These do
not establish the exclusive cause of every old or remaining probe timeout.

Original stack/SQL/CPU, p99, publication, quote-coverage, test-instrumentation and
code-audit reports/evidence are preserved byte-for-byte in the
[Git history](../README.md#historical-records). Use the original path with the pinned Git snapshot.
Historical unresolved items remain visible in the remaining-work register.
