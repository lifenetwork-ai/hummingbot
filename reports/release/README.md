# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `e00cef38…`: unchanged full baseline, soak3600/five faults, four targeted E2E groups on first attempts, enhancement90; five soak probes plus mixed API1/transition engine1 |
| [Retained rollback in Git](../README.md#historical-records) | `837e7997…`: previous full gate with disclosed retry; Git `ff6b85ded:reports/release/mm-runtime-followup-load.md` and corresponding evidence JSON |

Superseded unused `bf4e1f77…` is removed after the new gate. The still earlier
`f74d2a2a…` image was removed previously; its original
[measurement](admin-cleanup-state-load.md) and [evidence](admin-cleanup-state-load-evidence.json)
retain their historical scope.

Earlier releases, original checkpoint/expiry reports and partial/managed lifecycle
acceptance live in the [Git history](../README.md#historical-records). They retain their
original image/date scope and failures; they are not additional current-image gates.
Actual Demo evidence is separate in [Demo verification](../demo/README.md).
