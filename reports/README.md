# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

Current image **a6fe88c0…** packages the write-fence projection and reporting-copy
optimizations. It passes the unchanged full load/60-minute soak/five faults,
four targeted E2E groups, enhancement90 and a separate activity300 diagnostic.
Soak p99 **406.14 ms**, zero unexpected healthy errors, **24.26 MiB** retained
growth and **4,885 simulated fills** reconcile. Eleven soak engine probes and
three mixed engine probes recover without healthy child replacement. Burst p99
and measured probe frequency regress versus rollback **e00cef38…**, despite
component savings; no uniform speedup or continuous-quote claim. The separate
activity diagnostic records completed cycles and working-order point samples
during its two probes, with finite coverage limits. One unused image removed;
all container references/volumes retained. Prior reports/attempts remain in Git
`745a86e3c` and `ff6b85ded` at their original report/evidence paths.
[Portable evidence](release/mm-runtime-followup-load-evidence.json).

Source/TDD, rejected drafts and paired component costs retain their own scopes in
[runtime remediation](implementation/MM_RUNTIME_FOLLOWUP.md). Latest reporting-copy
source: native 2,788 + 11 subtests / related PG 36; preceding write-fence projection:
native 2,780 + 11 / full PG 556. They are now separately packaged/qualified by the
current image gate. No real Google/GCP, connected Demo or production deployment
is inherited from these local results.

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
