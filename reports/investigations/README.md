# Incident and code investigations

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

Current conclusions are in the [source/TDD report](../implementation/MM_RUNTIME_FOLLOWUP.md)
and [current image report](../release/mm-runtime-followup-load.md). `b090430c…`
passes native/E2E and the unchanged full load/soak/five faults. The confirmed
pre-existing signal/Event-lock hazard is fixed and demonstrated in paired real
Linux-image signal tests. The observed old shutdown's exact cause is not proven.

Three healthy engine probes recover in 1.06–1.12 s without child replacement.
No baseline MM activity overlay, so observation intervals are not measured quote
stoppage. Warmup p99 and retained growth rise versus the failed predecessor; other
latencies/probe count improve on AC. There is no exclusive performance/power cause.

The failed `96368970…` gate remains in Git `d0791e563`, older accepted `a6fe88c0…`
in `8544488ef`, at current release report/evidence paths. Older activity point
samples retain their own image/date scope. No failed attempt is silently passed.

The [ownership-read investigation](../implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
reproduces a read-COMMIT probe delay and tests a scoped driver change. Its first
isolation prototype is rejected for unbounded pool-reset callbacks. Source native/
PG checks and the subsequent unchanged image hour gate pass separately. These do
not establish the exclusive cause of every old or remaining probe timeout.

Original stack/SQL/CPU, p99, publication, quote-coverage, test-instrumentation and
code-audit reports/evidence are preserved byte-for-byte in the
[Git history](../README.md#historical-records). Use the original path with the pinned Git snapshot.
Historical unresolved items remain visible in the remaining-work register.
