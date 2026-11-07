# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

The latest full-load repeat on **a6fe88c0… passes** after quantile-export correction
in the test fixture: all four windows, the sixty-minute Random soak and five faults.
Mixed/burst/soak p99 **366.46 / 168.04 / 287.63 ms**; zero unexpected healthy errors,
4,884 exact simulated fills/fees/positions and 23.16 MiB/64 MiB retained growth.
Three healthy engine probes recover in 1.053–1.177 s without child replacement.
Host AC power differs from the preceding failed battery run; the improvement is
observational, not exclusive code attribution. Existing containers/volumes/images remain.

The failed repeat (mixed main/auxiliary p99 1,315.91/1,169.10 ms, stopped before
burst/soak/faults) remains in Git **`41d290393`** at the current release paths.
The earlier same-image full pass (soak p99 406.14 ms, 24.26 MiB retained growth,
4,885 simulated fills), four targeted E2E groups and separate enhancement/activity
diagnostics retain their original scope in Git **`dfad4a4d1`** at the current
report/evidence paths. Earlier
rollback reports/failures remain in Git `745a86e3c` and `ff6b85ded`.
[Portable evidence](release/mm-runtime-followup-load-evidence.json).

The follow-up [regression audit](implementation/MM_RUNTIME_FOLLOWUP.md#combined-image-regression-audit--2026-11-07)
compares immutable images in ABBA order and fixes a reproduced lock dependency
inside test incident capture. The probe-frequency attribution remains open;
production trading code is unchanged. The subsequent metrics-selection correction
has 80 frozen targeted passes before load and 81 after an additional precision case.
Both the failed attempt and new complete gate remain separately attributed.

Source/TDD, rejected drafts and paired component costs retain their own scopes in
[runtime remediation](implementation/MM_RUNTIME_FOLLOWUP.md). Latest reporting-copy
source: native 2,788 + 11 subtests / related PG 36; preceding write-fence projection:
native 2,780 + 11 / full PG 556. They are now separately packaged/qualified by the
earlier image gate. No real Google/GCP, connected Demo or production deployment
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
