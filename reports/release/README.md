# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `837e7997…`: unchanged full baseline, soak3600/five faults, targeted E2E with disclosed retry, enhancement90; eight recovered probes |
| [Retained rollback in Git](../README.md#historical-records) | `bf4e1f77…`: previous full gate; Git `9ca14a148:reports/release/mm-runtime-followup-load.md` and corresponding evidence JSON |

Earlier `f74d2a2a…` image is removed after the new gate; its original
[measurement](admin-cleanup-state-load.md) and [evidence](admin-cleanup-state-load-evidence.json)
retain their historical scope.

Earlier releases, original checkpoint/expiry reports and partial/managed lifecycle
acceptance live in the [Git history](../README.md#historical-records). They retain their
original image/date scope and failures; they are not additional current-image gates.
Actual Demo evidence is separate in [Demo verification](../demo/README.md).
