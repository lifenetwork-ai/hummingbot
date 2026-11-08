# Incident and code investigations

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

Current conclusions are in the [runtime remediation](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [current image report](../release/mm-runtime-followup-load.md). New image
`96368970…` has twelve recovered healthy probe transitions (ten engine/two API)
and **fails burst main/auxiliary p99**, while soak3600/five fault assertions pass.
No healthy child replacement occurs. Inclusive observer/recorder timings, GC and
sampled SQL stacks show overlap, not an exclusive latency cause. Bounded omitted
spans and absent baseline MM activity prevent complete quote-downtime attribution.

The preceding accepted gate remains in Git `8544488ef` at current release paths;
older failure/retry evidence remains in `41d290393`/`ff6b85ded`. Earlier separate
activity300 point samples retain their own image/date scope and do not instrument
this baseline retrospectively. Corrected `all` fails inventory shutdown after fixing
an older dummy-credential fixture omission; upgrade is unrun without a previous
compatible image. No current failure is silently passed by an older result.

The [ownership-read investigation](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces a read-COMMIT probe delay and tests a scoped driver change. Its first
isolation prototype is rejected for unbounded pool-reset callbacks. Source native/
PG checks and the subsequent unchanged image hour gate pass separately. These do
not establish the exclusive cause of every old or remaining probe timeout.

Original stack/SQL/CPU, p99, publication, quote-coverage, test-instrumentation and
code-audit reports/evidence are preserved byte-for-byte in the
[Git history](../README.md#historical-records). Use the original path with the pinned Git snapshot.
Historical unresolved items remain visible in the remaining-work register.

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
