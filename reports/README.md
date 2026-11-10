# Verification results

Start with the [Demo/release closeout](release/demo-release-closeout.md),
[portable evidence](release/demo-release-closeout-evidence.json) and
[remaining-work register](../docs/plans/README.md). Operational/API contracts
live in [docs](../docs/README.md).

Latest source `f53885fda`, regular image `4198f2fa…`: canonical native3,085 +16,
PostgreSQL605 and all30 packaged E2E pass. Full unchanged load/Random soak3600/six faults/retention
pass: main p99 96.87/96.46/103.11/283.41ms,4,885 simulated fills, growth18.02MiB.
Overall Cloud closeout still needs revision75 image/rollout verification. Actual Demo revision73 qualifies BUY/SELL
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

Current follow-up: API now reports revision75, same running session/control53,
Demo and regular catalogue, no HALT. Its image/environment and the01:41 UTC
rollout transition await GCP reauthentication; this is not continued evidence of
revision74. See [closeout investigation](release/demo-release-closeout.md#later-revision-transition-under-investigation).

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
