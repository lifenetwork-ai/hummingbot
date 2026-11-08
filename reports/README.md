# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

The new **`96368970…`** image packages the quote/anchor/recorder allocation
changes, but its **full load gate fails burst p99**: main **1,084.01 ms** and
auxiliary **1,151.07 ms**, both against 1,000 ms. Warmup/mixed/soak and all five
fault assertions pass. Soak p99 **547.30 ms**, 4,884 exact simulated fills,
26.09 MiB/64 MiB retained growth; twelve healthy probes recover without child
replacement. All 97,800 main / 24,450 auxiliary healthy reads complete HTTP200.
Battery-to-AC host conditions and cold-build dependencies differ from the prior
accepted image; exclusive attribution remains open.

Source verification: **1,010 native passes + 9 subtests** and paired component
measurements. The first E2E exposed missing dummy perpetual credentials in an
older live-mode seed fixture. TDD reproduces it, then 14 related native tests pass
with fixture commit `556d30bb7`. The corrected `all` fails inventory shutdown; targeted remaining groups pass;
upgrade remains unrun without a compatible prior image. Shipping runtime stays
build `8544488ef`; the host fixture correction does not change the engine.
[Current report](release/mm-runtime-followup-load.md),
[portable evidence](release/mm-runtime-followup-load-evidence.json),
[source/TDD](implementation/MM_RUNTIME_FOLLOWUP.md).

The preceding accepted `a6fe88c0…` quantile-fixture gate remains in Git
**`8544488ef`** at the same release report/evidence paths. Its mixed/burst/soak p99
was 366.46/168.04/287.63 ms with three recovered probes. Earlier failed attempts
remain in Git `41d290393` and `ff6b85ded`; previous scoped full/E2E/activity
results remain in `dfad4a4d1` and `745a86e3c`. These are historical records, not
images guaranteed to remain in the current daemon. No connected Demo, real
Google/GCP or production deployment is inherited from offline verification.

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

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](../docs/plans/MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
