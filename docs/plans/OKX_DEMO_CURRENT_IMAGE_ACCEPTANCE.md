# OKX Demo and current offline image acceptance

Updated 2026-11-09. Prior image-specific checklists, approvals, failures, natural
fills and REST proofs remain in the [acceptance history](../../reports/README.md#historical-records)
(Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`). A local pass never changes
the image/account/date attached to connected evidence.

## Current image — local/offline

- [x] Build/pin `96368970…` from `8544488ef`, verify 1,225 packaged hashes and
  four packaged counter/behavior contracts; size 1,374,595,562 bytes.
- [x] Run unchanged full baseline/security/operator load, sixty-minute Random
  soak and five faults. All 97,800 main/24,450 auxiliary healthy reads complete
  HTTP200; 4,884 simulated fills reconcile; retention 26.09 MiB/64 MiB.
- [ ] Full-performance gate: burst main/auxiliary p99 **1,084.01/1,151.07 ms**
  exceed 1,000 ms. Later passing windows do not close this failed gate.
- [x] Disclose twelve healthy recovered probe transitions, ten engine/two API,
  approximately 1.031–1.248 s, no healthy replacement. No baseline activity
  overlay; these intervals are not independently measured quote downtime.
- [x] TDD-fix the older initial-position fixture's missing dummy live perpetual
  credentials (`556d30bb7`); 14 related native tests pass, no runtime gate change.
- [ ] Complete corrected available-E2E matrix on the same immutable image.
- [ ] Upgrade/rollback E2E: unrun without a compatible prior image.

[Current image report](../../reports/release/mm-runtime-followup-load.md),
[portable evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
All trading is simulated with dummy credentials. The previous accepted
`a6fe88c0…` gate remains in Git `8544488ef` at those release paths; separate earlier
enhancement/activity diagnostics retain their original scope. No old bot/rollback
image or Demo container exists in the current daemon. Unrelated local images and
volume are preserved; no image prune occurs.

## Actual Demo acceptance

The [connected evidence index](../../reports/demo/README.md) and
[last documented stopped-Demo restoration](../../reports/performance/mm-admin-worker-lanes.md)
retain historical account/image scope. Earlier inventories preserved the then
existing `086454de…` Demo service and PostgreSQL; that statement does not describe
the present daemon. This run neither queries a real OKX account nor repairs,
starts or migrates a Demo ledger.

Current-image connected preflight, controlled sessions, natural fills and restart
have not been performed on `96368970…`. Historical positive fill/REST evidence and
unchecked controller-image spot/soak/guard-wait items keep their original dates
and images; new offline gates do not retroactively complete them.

- [ ] Reviewed existing-Demo managed-credential cutover, if selected. See the
  [credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md) and
  [PostgreSQL maintenance rules](../postgres-runtime.md).

Real Google/GCP, production quote-coverage policy and unresolved historical/probe
attribution are in the [remaining-work register](README.md).

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
