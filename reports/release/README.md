# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `a6fe88c0…` passes full baseline/Random soak3600/five faults with quantile fixture `41d290393`; mixed/burst/soak p99 366.46/168.04/287.63 ms, 4,884 simulated fills, retained growth 23.16 MiB and three recovered healthy engine probes. Previous failed attempt remains in Git `41d290393`; older full pass/E2E/diagnostics in Git `dfad4a4d1`. |
| [Retained rollback](../README.md#historical-records) | `e00cef38…`: previous unchanged full gate; Git `745a86e3c:reports/release/mm-runtime-followup-load.md` and corresponding evidence JSON. Earlier shutdown failure/retry remains in Git `ff6b85ded` |

Superseded unused `837e7997…` is removed after the new gate; candidate and rollback remain. The still earlier
`f74d2a2a…` image was removed previously; its original
[measurement](admin-cleanup-state-load.md) and [evidence](admin-cleanup-state-load-evidence.json)
retain their historical scope.

Earlier releases, original checkpoint/expiry reports and partial/managed lifecycle
acceptance live in the [Git history](../README.md#historical-records). They retain their
original image/date scope and failures; they are not additional current-image gates.
Actual Demo evidence is separate in [Demo verification](../demo/README.md).
