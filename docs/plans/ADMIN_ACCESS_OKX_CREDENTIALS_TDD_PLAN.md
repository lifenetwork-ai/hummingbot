# Admin access and exchange credentials — delivery status

A1–A6 local/offline implementation is complete. Full requirements, API matrices,
Red/Green history and failed/scoped acceptance attempts are retained in the
[original TDD plan](../../reports/README.md#historical-records) (Git: `docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md`). Runtime behavior and exact settings are defined
in [credential operations](../exchange-credentials.md) and
[FE integration](../fe-integration.md).

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../../reports/release/demo-release-closeout.md#later-revision-transition-under-investigation).

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

Latest Demo qualification is recorded in [Demo/release closeout](DEMO_RELEASE_CLOSEOUT.md):
source `f53885fda`, native3,085 +16, PostgreSQL605 and all30 packaged E2E pass.
Full unchanged load/Random soak3600/six faults/retention pass.
Historical rollout attribution is closed; latest merged CI artifact verification remains open. Actual revision73 qualifies perpetual
BUY/SELL handoff and the30-minute conditional quote target; real automatic
close/remove/Set and384-fill stopped readback pass. Regular revision74 is left
running Until stopped. Earlier real Google recovery remains qualified on
revision65 in the [recovery plan](ADMIN_RECOVERY_API_TDD_PLAN.md).
Real GCP KMS/IAM, actual FE and live cutover remain separate.

- [x] Native source/PG, local encryption, offline signed Google identities, role/
  audit and lifecycle/fault evidence. Original image/date scopes remain in Git.
- [x] Earlier full offline gate on `b090430c…`: canonical native 2,861 + 16 subtests, all 27 available
  E2E in one all execution, full unchanged security/operator load/soak3600/five
  faults. No unexpected healthy errors or healthy child replacement; 4,885 exact
  simulated fills/fees/positions. Separate technical upgrade/rollback passes.
- [x] TDD-confirm/fix pre-existing signal/Event-lock hazard (`1916e048e`) and verify
  old/new Linux-image behavior; original observed shutdown's unique cause remains
  unproven. Preserve failed963 gate in Git `d0791e563`, accepted a6 in `8544488ef`.
  [Current report](../../reports/release/mm-runtime-followup-load.md).
- [x] Prepare loopback-only real-Google sign-in helper with the existing verifier,
  access handlers and audit; isolate a local auth DB, block trading routes, and
  check real Google public-certificate discovery (HTTP 200, two certificates on
  2026-11-09). Helper/identity suite: 53 passes; browser checks are recorded below.
- [x] Real Google browser sign-in with the supplied client succeeds in the
  isolated auth database. Bound active `admin`, matching email, correlated
  `account_bound` and `login` audit verified at `2026-11-08T22:07:39Z`.
- [x] Disable Basic in the helper's private env and restart it. The previous
  Basic credential returns HTTP 401 `basic_auth_disabled`; trading `.env` is
  unchanged. Real public-certificate discovery also succeeds.
- [x] Real Google browser login after Google-only helper restart succeeds at
  `2026-11-08T22:09:59.629386Z`. Operator result and durable login audit agree;
  Basic remains rejected. No ID token is requested or saved to evidence.
  [Acceptance evidence](../../reports/implementation/google-login-acceptance.json).
- [ ] Real GCP KMS/IAM using synthetic credentials when resources are provided.
  User deferred this; local encryption does not close it.
- [x] Real Cloud Run Google admin onboarding and credentials lifecycle on2026-11-09:
  invited email bound through operator browser sign-in; Set both slots, Start,
  bot-wide cleanup/per-slot revoke, Set again and Start/Stop all use the verified
  Google actor. Five actual fills/fees/positions match REST; final slotsv2,
  stopped/flat/pending0. [Cloud result](../../reports/demo/cloudrun-google-credentials-acceptance.md).
  This does not close real GCP KMS, full dashboard, load or quote-availability gates.
- [x] Explicitly reviewed actual-Demo account binding was applied without editing
  old history/positions. Both managed slot Set/load and stopped cold restart pass.
  Nine fixed profile/mode cases pass; three new natural spot fills have individual
  REST/aggregate quantity and original-fee proof. No production deployment.
- [x] Separately reviewed recovery and connected follow-up pass on `4451974e…`: 'both'
  Random steps0/1/2/all profiles, active restart, Stop, real cancel/close/remove,
  API reconfigure and stopped cold restart. Seven cleanup fill parts close both
  managed hedge sides; original history and spot cash remain, no duplicate fills.
  [Connected evidence](../../reports/demo/managed-local-connected-followup.md).
  [Runtime follow-up](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md#managed-demo-random-shutdown-follow-up--2026-11-09).

Earlier `b090430c…` has three healthy engine probes recovering in 1.06–1.12 s. Baseline activity overlay is off,
so these are not measured quote-stop intervals. No exclusive performance/power
cause or qualified production rollback is inferred. Remaining policy/evidence
items are centralized in the [register](README.md).
