# Incident and code investigations

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

Current conclusions are in the [runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [current image report](../release/mm-runtime-followup-load.md). Current image
`a6fe88c0…` has eleven recovered soak engine probes and three mixed engine probes,
without healthy child replacement. Probe frequency and burst p99 regress versus
rollback `e00cef38…`; unchanged budgets still pass. Supervisor intervals are not
quote downtime. Separate activity300 records completed cycles and two-sided
working-order point samples during its two probes, but also non-two-sided refresh
observations; it does not establish continuous quotes or full-soak activity.

The previous accepted report remains in Git `745a86e3c`; the still earlier failed
shutdown poll and unchanged retry remain in Git `ff6b85ded`. Current targeted
E2E passes first attempts. No exclusive historical or current probe cause is
established; bounded captures cannot reconstruct unrecorded activity.

The [ownership-read investigation](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces a read-COMMIT probe delay and tests a scoped driver change. Its first
isolation prototype is rejected for unbounded pool-reset callbacks. Source native/
PG checks and the subsequent unchanged image hour gate pass separately. These do
not establish the exclusive cause of every old or remaining probe timeout.

Original stack/SQL/CPU, p99, publication, quote-coverage, test-instrumentation and
code-audit reports/evidence are preserved byte-for-byte in the
[Git history](../README.md#historical-records). Use the original path with the pinned Git snapshot.
Historical unresolved items remain visible in the remaining-work register.
