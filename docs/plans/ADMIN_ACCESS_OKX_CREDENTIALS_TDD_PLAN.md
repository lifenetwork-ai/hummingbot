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
- [x] Previous image `a6fe88c0…` qualified with its original fixture scope;
  evidence remains in Git `8544488ef` at current release paths.
- [x] New `96368970…` runs unchanged security/operator load and passes soak3600/
  five fault assertions; 4,884 simulated fills reconcile, no healthy replacement.
- [ ] New image full-performance acceptance: burst main/auxiliary p99
  1,084.01/1,151.07 ms exceed 1,000 ms. Corrected `all` fails inventory shutdown; remaining targets pass;
  upgrade is unrun without a compatible previous image.
  [Current result](../../reports/release/mm-runtime-followup-load.md).
- [ ] Real Google login/certificate discovery and real GCP KMS/IAM using synthetic
  credentials when resources are provided. Local/offline verification cannot close this.
- [ ] Existing-Demo managed-credential cutover if chosen, under the documented
  operator review and account/ledger checks. No new cutover or production deployment
  was performed by the latest local tests.

The Basic-disable/identity, deployment routing, account binding and cleanup rules
remain unchanged by this documentation consolidation. [Remaining-work register](README.md).

Shutdown follow-up: corrected `all` E2E passes fourteen groups before a nonzero
inventory-skew shutdown; all thirteen remaining/targeted groups pass, covering twenty-seven unique
groups across attempts. The failed `all` remains failed. A real-signal
TDD reproducer confirms an Event-lock hazard, fixed in source `1916e048e` (75 related
passes). This is a possible mechanism, not proof of the old exit's unique cause.
`96368970…` predates the fix; new-image qualification remains open. See the
[runtime checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md).
