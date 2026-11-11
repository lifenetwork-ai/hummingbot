# Verification results

Start with the [Demo/release closeout](release/demo-release-closeout.md),
[portable evidence](release/demo-release-closeout-evidence.json) and
[remaining-work register](../docs/plans/README.md). Operational/API contracts
live in [docs](../docs/README.md).

The 2026-11-11 [runtime baseline / connected handoff follow-up](demo/cloudrun-runtime-baseline-handoff.md)
records the newly verified legacy ledger, shutdown-state presentation fix and
the separately measured quote window. Older release results below retain their
original image and date scope. The [perpetual cap follow-up](demo/cloudrun-perpetual-cap-qualification.md)
tracks the revised Demo exposure limits and the regular-profile observation.

The [quiet-feed / unsent-quote follow-up](performance/mm-feed-quote-recovery.md)
keeps its targeted offline evidence separate from the historical full release.

Last full release baseline `f53885fda`, regular image `4198f2fa…`: canonical native3,085 +16,
PostgreSQL605 and all30 packaged E2E pass. Full unchanged load/Random soak3600/six faults/retention
pass: main p99 96.87/96.46/103.11/283.41ms,4,885 simulated fills, growth18.02MiB.
Historical rollout attribution is closed; latest merged CI artifact verification remains open. Actual Demo revision73 qualifies BUY/SELL
perpetual handoff and the30-minute conditional quote target:99.7700% spot,
99.7133% perpetual, zero unknown. Automatic close/remove/Set and384 stopped actual
fills/original fees/positions reconcile. Regular catalogue is restored on
revision74 and separately started53, both/follow_market, Until stopped, no HALT.

The [preceding full offline result](release/mm-runtime-followup-load.md) remains
scoped to `b090430c…`: native2,861 +16,27 E2E groups, unchanged load/Random
soak3600/five faults,4,885 simulated fills. Earlier connected lifecycle, Google
and recovery records remain in the [Demo index](demo/README.md). No pass is
transferred across images or from simulated fills to actual Demo trades.

| Topic | Current entry |
| --- | --- |
| [Release](release/README.md) | Current candidate and retained rollback, exact image/gate scope |
| [Implementation](implementation/README.md) | Concise delivery status and latest TDD/source verification |
| [Performance](performance/README.md) | Current optimization, memory and economic measurements |
| [Demo](demo/README.md) | Actual OKX evidence, approvals and image/account scope |
| [Platform](platform/README.md) | Cloud preparation and operating-guide links |
| [Investigations](investigations/README.md) | Current limits and historical incident lookup |
| [Git history](#historical-records) | Original completed plans, reports and full journals |

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](release/demo-release-closeout.md#later-revision-transition-under-investigation).

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
