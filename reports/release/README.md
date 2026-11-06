# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `bf4e1f77…`: unchanged full baseline, soak3600/five faults, four targeted E2E groups, enabled-enhancement90; scoped probe/latency limits |
| [Retained rollback](admin-cleanup-state-load.md), [evidence](admin-cleanup-state-load-evidence.json) | `f74d2a2a…`: previous full gate; original failed attempts and six recovered soak probes retained |

Earlier releases, original checkpoint/expiry reports and partial/managed lifecycle
acceptance live in the [history archive](../archive/README.md). They retain their
original image/date scope and failures; they are not additional current-image gates.
Actual Demo evidence is separate in [Demo verification](../demo/README.md).
