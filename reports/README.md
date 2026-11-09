# Verification results

Start with the [current image/load report](release/mm-runtime-followup-load.md)
and [remaining-work register](../docs/plans/README.md). Operational procedures and
API/FE contracts live in [docs](../docs/README.md).

Last full offline image **`b090430c…`** passes canonical native **2,861 + 16 subtests**,
all **27 available E2E groups in one `all` attempt**, four unchanged load windows,
Random soak3600 and five faults. Mixed/burst/soak p99 **273.48 / 297.81 / 313.00 ms**;
zero unexpected healthy errors, **4,885 simulated fills** reconcile, retained
growth **30.33 MiB / 64 MiB**. Three healthy engine probes recover in 1.06–1.12 s
without healthy replacement. Original budgets remain.

Runtime fix `1916e048e` removes a confirmed pre-existing signal/Event-lock hazard;
paired Linux-image real-signal contracts reproduce old exit1/new exit0. All 1,225
packaged hashes match build `d0791e563`; 1,254 measured source/fixture hashes stay
frozen. The observed run is AC. Healthy latency differences are not uniquely
attributed to the shutdown-only correction or to power.

[Current report](release/mm-runtime-followup-load.md),
[portable evidence](release/mm-runtime-followup-load-evidence.json),
[source/TDD](implementation/MM_RUNTIME_FOLLOWUP.md). The failed `96368970…` burst
and `all` shutdown attempts remain in Git **`d0791e563`** at the same release
paths; older accepted `a6fe88c0…` records remain in **`8544488ef`**. Neither failed
predecessor is retrospectively passed. That offline qualification did not contact
real Demo/Google/GCP or deploy a service.

Separate 2026-11-09 follow-up: [real Google login](implementation/google-login-acceptance.json)
and Google-only helper restart pass. Managed Demo binding/Set/load/nine fixed cases
pass on the preceding image, followed by a failed Random cleanup attempt. After
TDD remediation and separately approved HALT recovery, the [connected follow-up](demo/managed-local-connected-followup.md)
passes on `4451974e…`: all profiles through Random steps0/1/2, active restart,
Stop, real close/remove/reconfigure and stopped cold restart. Old history and
spot cash remain, perpetual flat/pending orders0, no HALT. Three natural spot
fills plus seven cleanup fill parts match individual REST. One optional cleanup
reporting warning recovers; its synthetic-name cause is now fixed in source by
TDD (native96/PG109), not yet packaged/retested on Demo. Next candidate packaging/
full release/soak and real GCP KMS remain open. [Quote diagnostics](performance/mm-managed-local-quote-activity.md)
distinguish policy agreement from thirty-minute coverage qualification.

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
