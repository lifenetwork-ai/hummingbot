# Admin access and encrypted credential acceptance

Status: A1–A5 committed locally. A6 final-image acceptance is in progress; do not
transfer acceptance from an older image or treat simulated Google/OKX as external
acceptance. Actual Google OAuth/GCP KMS resources are not available yet, as confirmed
by the operator. The existing Demo service/database have not been cut over.

Runtime implementation commits: `c06c02a60`, `91cc55267`, `69e16e142`, `7c5d99588`,
`4f284b9f8`. FE/runbook handoff: `f98c4711a`.

## Completed local checks

- Full native release regression: **2,628 tests passed**, plus 11 subtests, using
  `make test-cloudrun-regression`. Original command populations: 202, 11, 1,700, 715.
  New crypto/adapter groups are now included in that Make target; the focused
  follow-up passed **38 tests**. These follow-up scopes overlap other earlier tests.
- Security/load wrapper checks passed **47 tests** before adding the simulator
  reduce-close cases; the later simulator/identity-focused follow-up passed **16**.
- Earlier A4b verification, failed Red/development attempts and local runtime limits
  are recorded in [phase progress](../implementation/ADMIN_ACCESS_CREDENTIALS_PROGRESS.md).

Full PostgreSQL suite: **439 passed**, 36 existing warnings, in 846.10 seconds.
Log: `/tmp/hbot-admin-release-postgres.log`.
Image build completed for `hummingbot-cloudrun:admin-credentials-f98c4711a`: config ID
`sha256:37c36234ca89b4a6c5a9dc1be89c453806339fd651197c57caf8bd58ba08c630`,
1,374,389,795 bytes. Immutable registry digest is still pending.
Build log: `/tmp/hbot-admin-release-build.log`.
Native log: `/tmp/hbot-admin-release-native.log`.

The first image probe passed its diagnostic load window but failed automatic
Disconnect before any close transport because of an unregistered native throttle
bucket. The Red/Green fix is committed as `4eb311ff5`; acceptance must use a rebuilt
image. The full native/PG numbers above predate that narrow runtime fix; focused
runtime follow-up passed 17 tests. Failed attempt: `build/mm-load/20261105-081017-a5bc18`.

## Final-image work pending

- Build `hummingbot-cloudrun:admin-credentials-f98c4711a`, record immutable digest.
- Run the complete existing image E2E and retention gates, preserving their limits.
- Run the bounded Google-only/local-encrypted-key probe, then unchanged long load
  profiles including the sixty-minute zero-error soak.
- Exercise real native connector/recorder automatic cancel/close/removal and restart
  against the isolated exchange; compare actual simulated fills/fees and checkpoints.
- Preserve every failed attempt and distinguish harness defects, application defects
  and unresolved performance evidence.

The optional `--security` fixture signs offline Google-shaped tokens and injects
only public verification certificates inside the mounted test wrapper. Signature,
audience, issuer and expiry checks remain real; no shipping bypass flag is added.
Accounts and role bindings are created through actual HTTP handlers. Basic is disabled
before measurement, and working exchange environment keys are removed: the engine must
load local-encrypted PostgreSQL bundles. Every normal request still performs current
role lookup. The bounded additional workload checks invalid auth, credential/audit
reads and demotion/re-promotion on the same signed token. Original read/fill schedules,
budgets and soak duration are unchanged. Private fixture signing keys are not persisted.

## External checks explicitly open

Real Google sign-in/certificate discovery and actual GCP KMS/IAM with synthetic
credentials require the operator's resources. Existing Demo cutover needs its separate
reviewed procedure and ledger/account binding; no production deployment is performed.

A harness regression check rejected wrapping the seed callback with `partial`:
the existing static safety gate requires the original generator to pass directly
through the verified offline preparation lifecycle. The original callback was
restored; optional audit-history seeding reads only the explicit test fixture flag.
The guard was retained unchanged and the affected harness scope was rerun.

Post-fix native regression on runtime `4eb311ff5` passed **2,671 tests** plus 11
subtests (202, 11, 1,705, 753). The second full PostgreSQL attempt was stopped after
four known failures in the shared test seeder: SQL-only fixture adapters lacked the
optional `env` attribute. It finished with 4 failed/78 passed before interruption;
this was not a trading-query failure or a clean full-suite pass. A minimal-adapter
Red/Green regression now covers that compatibility and optional audit-history mode.
The four affected PostgreSQL cases passed on rerun; a clean full rerun remains due.
Candidate runtime image: `8cd8052d…`, tag `admin-credentials-fc3577831`.

The shared SQL-only seeder compatibility regression passed **32 native preparation
checks** and all **4 affected PostgreSQL cases**. The test fixture's extra audit
history remains opt-in; its original generator and offline-only lifecycle are intact.

## Corrected-image diagnostic probe

`build/mm-load/20261105-082520-d84e71` passed the Google-only/managed-key probe:
20 reads/s, 100.002 updates/s, 1 fill/s, p95 63.18 ms, p99 343.25 ms, no declared
window violations; sampled RSS 627,216,384 bytes. Synthetic audit history contained
100,000 entries. The bounded role/denial workload ran alongside the original slots.
The native connector/recorder cleanup then closed LONG and SHORT, reconciled all
62 simulated fills/fees, removed the selected slots sequentially and restarted.
Spot checkpoints were preserved and no removed key or closed exposure returned.
This is a 60-second diagnostic, **not** the required sixty-minute release soak.
Actual Google/GCP/OKX Demo remain explicitly outside this evidence.

Clean PostgreSQL rerun: `/tmp/hbot-admin-release-postgres3.log` (running).
Full image E2E/upgrade: `/tmp/hbot-admin-release-e2e.log` (running).

The bounded diagnostic capture retained one 311.093 ms engine probe timeout before
the measured probe window (epoch 1793842006.25, recovered 1793842007.61; window
1793842017.39–1793842077.39). There were zero captured probe failures or loop-stall
triggers inside that window. Startup/restart signals are retained, not called a
root-cause fix or silently erased by the healthy window result.

The clean post-fix PostgreSQL rerun passed **439/439** in 837.56 seconds
(`/tmp/hbot-admin-release-postgres3.log`). Two additional full-supervisor provider
fault tests passed separately: delayed decryption exceeds normal observer freshness
without a restart, and permission denial stays explicit while API/live health remains
available with no environment fallback/restart loop. Provider behavior is injected
only into the native test wrapper; this is not real GCP acceptance.
The existing 45-second stopped-load health grace required no production change.
Focused fault log: `/tmp/hbot-admin-provider-fault-process.log`.
