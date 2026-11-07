# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `a6fe88c0…`: unchanged full baseline, soak3600/five faults, four targeted E2E groups, enhancement90/activity300; soak engine11/mixed engine3 recover; burst p99/probe frequency regress |
| [Retained rollback](../README.md#historical-records) | `e00cef38…`: previous unchanged full gate; Git `745a86e3c:reports/release/mm-runtime-followup-load.md` and corresponding evidence JSON. Earlier shutdown failure/retry remains in Git `ff6b85ded` |

Superseded unused `837e7997…` is removed after the new gate; candidate and rollback remain. The still earlier
`f74d2a2a…` image was removed previously; its original
[measurement](admin-cleanup-state-load.md) and [evidence](admin-cleanup-state-load-evidence.json)
retain their historical scope.

Earlier releases, original checkpoint/expiry reports and partial/managed lifecycle
acceptance live in the [Git history](../README.md#historical-records). They retain their
original image/date scope and failures; they are not additional current-image gates.
Actual Demo evidence is separate in [Demo verification](../demo/README.md).
