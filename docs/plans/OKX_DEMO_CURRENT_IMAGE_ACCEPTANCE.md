# OKX Demo and current offline image acceptance

Updated 2026-11-07. Prior image-specific checklists, approvals, failures, natural
fills and REST proofs remain in the [original acceptance history](../../reports/README.md#historical-records) (Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`).
A local pass never changes the image/account/date attached to connected evidence.

## Current image — local/offline

- [x] Build/pin `a6fe88c0…`, verify 1,224 packaged source/config hashes and keep
  them unchanged through testing; includes write-fence projection/reporting copy.
- [x] Runtime ABI/JIT/TLS/SDK/size smoke and four targeted E2E groups: availability,
  drawdown, MM recovery/DB restart and initial-position restart. All first attempts.
- [x] Enabled-enhancement90: 90 exact simulated fills/fees/positions, offline
  identities/local encryption and operator reads.
- [x] Unchanged full baseline, 60-minute Random soak/five faults: 97,800 main +
  24,450 auxiliary healthy HTTP200, zero unexpected healthy errors, soak p99
  406.14 ms, 24.26 MiB retained growth and 4,885 exact simulated fills.
- [x] Disclose eleven recovered soak engine probes and three mixed engine probes;
  probe frequency/burst p99 regress versus rollback `e00cef38…` within unchanged
  budgets. Intervals are supervisor observations, not quote downtime.
- [x] Separate activity300: completed cycles and two-sided venue point samples
  during two probes; non-two-sided refresh samples and coverage limits retained.
  This does not prove continuous quoting or add activity traces to baseline soak.
- [x] Cleanup unused `837e7997…`; keep candidate `a6fe88c0…` / rollback `e00cef38…`, all existing
  container references and volumes. Earlier evidence stays in Git `745a86e3c`;
  the older shutdown failure stays in `ff6b85ded`.

[Image report](../../reports/release/mm-runtime-followup-load.md),
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
This is a simulator run with dummy credentials, not a connected OKX Demo run or
complete E2E/upgrade matrix.

## Existing actual Demo

The pre/post inventories preserve the existing service on `086454de…` and its
PostgreSQL container, including IDs, image references and start timestamps.
The latest local run does not query or repair that Demo ledger, start a trading
session there, or replace its image/configuration. The
[last documented stopped-Demo restoration](../../reports/performance/mm-admin-worker-lanes.md)
and [connected evidence index](../../reports/demo/README.md) retain their own scope.

Current-image connected preflight, controlled sessions, natural fills and restart
on `a6fe88c0…` have not been performed by this task. Existing historical positive
fill/REST evidence stays attributed to its original images. Historical unchecked
controller-image spot/soak/guard-wait items are preserved in the original plan;
newer local gates do not retroactively complete them.

- [ ] Reviewed existing-Demo managed-credential cutover, if selected. See the
  [credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md) and
  [PostgreSQL maintenance rules](../postgres-runtime.md).

Real Google/GCP, production quote-coverage policy and unresolved historical/probe
attribution are listed in the [remaining-work register](README.md).
