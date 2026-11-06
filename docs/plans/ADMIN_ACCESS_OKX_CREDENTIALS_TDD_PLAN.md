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
  audit and simulator lifecycle/fault tests. Prior lifecycle cases retain their
  original image identities in Git history; they are not relabelled as new runs.
- [x] Current image `bf4e1f77…`: full unchanged baseline/security/operator traffic,
  60-minute soak, five faults, four targeted E2E groups and enabled-enhancement90.
  [Current result](../../reports/release/mm-runtime-followup-load.md).
- [ ] Real Google login/certificate discovery and real GCP KMS/IAM using synthetic
  credentials when resources are provided. Local/offline verification cannot close this.
- [ ] Existing-Demo managed-credential cutover if chosen, under the documented
  operator review and account/ledger checks. No new cutover or production deployment
  was performed by the latest local tests.

The Basic-disable/identity, deployment routing, account binding and cleanup rules
remain unchanged by this documentation consolidation. [Remaining-work register](README.md).
