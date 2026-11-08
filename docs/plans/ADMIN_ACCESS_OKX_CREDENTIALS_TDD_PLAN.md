# Admin access and exchange credentials — delivery status

A1–A6 local/offline implementation is complete. Full requirements, API matrices,
Red/Green history and failed/scoped acceptance attempts are retained in the
[original TDD plan](../../reports/README.md#historical-records) (Git: `docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md`). Runtime behavior and exact settings are defined
in [credential operations](../exchange-credentials.md) and
[FE integration](../fe-integration.md).

## Delivered requirements

- [x] Optional environment-controlled Basic bootstrap, Google identity verification,
  allowlisted accounts and fresh `admin` / `write` / `read` role enforcement.
  Basic can be disabled after account provisioning; mutations recheck permissions.
- [x] Admin/write may Set or Disconnect scoped exchange credentials. Admin manages
  accounts/roles. Status and error guidance is English and secret-free.
- [x] `EXCHANGE_CREDENTIALS_SOURCE=postgres_kms`, with explicit `KMS_PROVIDER=local|gcp`;
  no fallback to another provider, environment key or file after managed load fails.
- [x] Envelope encryption bound to bot/exchange/connector/environment/account/version.
  Database ciphertext, wrapping material and credential values have no read API.
- [x] Durable correlated audit for actor, action, outcome and operation; paginated
  audit API. No plaintext credential or key enters audit/status/log responses.
- [x] Set/rotation verifies identity and scope. Account changes do not attach a new
  account to an existing ledger silently. Demo/Live routing stays deployment-owned.
- [x] Disconnect stops new submissions, cancels bot-owned pending orders and closes
  managed perpetual exposure before removing stored credentials. Spot holdings
  remain. Partial/unknown outcomes retain recoverable status and never report
  premature completion. Deleting the API key on OKX remains an exchange-side action.
- [x] Cleanup/Retry/Repair/rotation preserve owner/version/terminal/fill proof across
  lost acknowledgements, failed commits, partial fills, restarts and dependencies.
- [x] Reuse existing exchange adapters, PostgreSQL audit, bounded API workers and
  trading engine. The current adapter is OKX; shared envelope/operation structure
  supports future adapters without claiming those exchanges implemented.

## Verification and remaining acceptance

- [x] Native source/PG, local encryption, offline signed Google identities, role/
  audit and lifecycle/fault evidence. Original image/date scopes remain in Git.
- [x] Current image `b090430c…`: canonical native 2,861 + 16 subtests, all 27 available
  E2E in one all execution, full unchanged security/operator load/soak3600/five
  faults. No unexpected healthy errors or healthy child replacement; 4,885 exact
  simulated fills/fees/positions. Separate technical upgrade/rollback passes.
- [x] TDD-confirm/fix pre-existing signal/Event-lock hazard (`1916e048e`) and verify
  old/new Linux-image behavior; original observed shutdown's unique cause remains
  unproven. Preserve failed963 gate in Git `d0791e563`, accepted a6 in `8544488ef`.
  [Current report](../../reports/release/mm-runtime-followup-load.md).
- [ ] Real Google login/certificate discovery and real GCP KMS/IAM using synthetic
  credentials when resources are provided. User deferred this; offline checks do
  not close it.
- [ ] Actual-Demo managed-credential cutover if chosen, with the documented account/
  ledger review. No new real-account cutover, DB repair or production deployment
  is performed by this local task.

Three healthy engine probes recover in 1.06–1.12 s. Baseline activity overlay is off,
so these are not measured quote-stop intervals. No exclusive performance/power
cause or qualified production rollback is inferred. Remaining policy/evidence
items are centralized in the [register](README.md).
