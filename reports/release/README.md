# Release verification

[All reports](../README.md) · [Remaining work](../../docs/plans/README.md)

| Report | Scope |
| --- | --- |
| [Current image/load](mm-runtime-followup-load.md), [evidence](mm-runtime-followup-load-evidence.json) | `96368970…`: **burst p99 fails** (main 1,084.01 / auxiliary 1,151.07 ms). Warmup/mixed/soak3600/five faults pass; 4,884 simulated fills, 26.09 MiB retained growth, twelve recovered healthy probes. Corrected `all` fails shutdown; targeted remaining groups pass; upgrade unrun. |
| [Previous accepted record](../README.md#historical-records) | `a6fe88c0…`, Git `8544488ef` at the current report/evidence paths: quantile-fixture full gate passes. No previous bot/rollback image exists in the current daemon. |

Earlier release attempts, checkpoint/expiry reports and partial/managed lifecycle
acceptance remain in Git history with their original failures and image/date
scope. This verification preserves unrelated local images/volume; no image prune
or connected Demo activity occurs. Actual exchange evidence is separate in
[Demo verification](../demo/README.md).

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](../../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
