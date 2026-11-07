# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

Current image **e00cef38…** passes the unchanged full load/60-minute soak/five
faults, four targeted E2E groups on first attempts and enabled-enhancement90.
Soak p99 **410.20 ms**, zero unexpected healthy errors, **28.59 MiB** retained
growth and **4,884 simulated fills** reconcile. Five soak engine probes, one
mixed API probe and one engine probe between windows recover; no healthy ERROR
or child replacement. Mixed/burst and probe counts improve versus `837e7997…`,
while warmup/soak latency and retention regress within unchanged budgets. No
uniform speedup or continuous-quote claim. One unused image removed; all container
references/volumes retained. The preceding report and failed shutdown attempt
remain in Git `ff6b85ded` under the same report/evidence paths.
[Portable evidence](release/mm-runtime-followup-load-evidence.json).

The [fill-flush source follow-up](implementation/MM_RUNTIME_FOLLOWUP.md#probe-follow-up-fill-autoflush--2026-11-07)
has separate native/PG verification, including a corrected obsolete query-count
assertion. This image gate identifies its own build and packaged source hashes.

The [ownership-read source verification](implementation/MM_RUNTIME_FOLLOWUP.md#residual-probes-ownership-reads--2026-11-07)
passes native2,780 + 11 subtests and full PG547. It removes a controlled redundant
read-COMMIT delay, rejects unbounded pool bookkeeping, and preserves transactional
writes/fresh fences. It does not uniquely attribute the old probe count increase
or replace the separately measured image qualification above.

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
