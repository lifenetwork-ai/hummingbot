# Incident and code investigations

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

Current conclusions are in the [runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [current image report](../release/mm-runtime-followup-load.md). Eight short
healthy probe episodes recover; their intervals are supervisor observations, not
quote downtime. Warmup/mixed improve while burst/soak and probe count regress
versus the preceding run; no uniform speedup is claimed. The first shutdown poll
timeout remains unexplained despite a passing unchanged retry. No unique historical
probe/p99/publication trigger is established; bounded captures cannot reconstruct
unrecorded MM activity.

The newer [ownership-read investigation](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces a read-COMMIT probe delay and tests a scoped driver change. Its first
isolation prototype is rejected for unbounded pool-reset callbacks. This source
work does not establish the exclusive inter-run probe cause or replace the
accepted image's evidence.

Original stack/SQL/CPU, p99, publication, quote-coverage, test-instrumentation and
code-audit reports/evidence are preserved byte-for-byte in the
[Git history](../README.md#historical-records). Use the original path with the pinned Git snapshot.
Historical unresolved items remain visible in the remaining-work register.
