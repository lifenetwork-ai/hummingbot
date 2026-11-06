# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

Current image **bf4e1f77…** passes the unchanged full load/60-minute soak/five faults,
four targeted E2E groups and enabled-enhancement90. Soak p99 **306.91 ms**, zero
unexpected healthy errors, **21.89 MiB** retained growth and **4,885 simulated fills**
reconcile. Three healthy probe episodes recover; no healthy ERROR/child restart.
Mixed/burst timings are slower than the prior run; attribution/continuity limits
remain explicit. One unused image removed; all container references/volumes retained.
[Portable evidence](release/mm-runtime-followup-load-evidence.json).

The newer [fill-flush source follow-up](implementation/MM_RUNTIME_FOLLOWUP.md#probe-follow-up-fill-autoflush--2026-11-07)
has separate native/PG verification, including a corrected obsolete query-count
assertion. It has not replaced this accepted image or inherited its hour gate.

| Topic | Current entry |
| --- | --- |
| [Release](release/README.md) | Current candidate and retained rollback, exact image/gate scope |
| [Implementation](implementation/README.md) | Concise delivery status and latest TDD/source verification |
| [Performance](performance/README.md) | Current optimization, memory and economic measurements |
| [Demo](demo/README.md) | Actual OKX evidence, approvals and image/account scope |
| [Platform](platform/README.md) | Cloud preparation and operating-guide links |
| [Investigations](investigations/README.md) | Current limits and historical incident lookup |
| [Git history](#historical-records) | Original completed plans, reports and full journals |

## Historical records

The archive folder and compressed snapshots have been removed. Original
JSON/Markdown/SQL evidence, image/date-specific failures, full plans and journals
remain in Git at **`4ed4365cd86cd06018d81432343c3cbb9aa34b52`**, before consolidation.
Current links identify the original repository path where relevant.

```sh
# Inspect an original record without restoring files into the checkout:
git show 4ed4365cd:docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md
git show 4ed4365cd:reports/release/mm-checkpoint-final-release.md
# Find a historical path:
git ls-tree -r --name-only 4ed4365cd -- docs/plans reports
```

The consolidation commit `b7da9f93d` also retains the complete archive snapshot in
Git history. No Git history rewrite/prune is performed. Historical unchecked boxes
and scope limits are retained; removing files does not close unresolved items.
Read the [remaining-work register](../docs/plans/README.md) for current status.
Raw logs/ledgers remain under ignored `build/`; private credentials stay outside Git.
