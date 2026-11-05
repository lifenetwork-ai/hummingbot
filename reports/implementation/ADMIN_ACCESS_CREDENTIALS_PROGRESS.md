# Admin access and exchange credentials implementation

Scope: [approved TDD plan](../../docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
Implementation started 2026-11-05 (Asia/Saigon). This evidence does not approve
production deployment or credential cutover on the running Demo account.

## A1 — Authentication and storage foundation

Implemented optional Basic Auth with an explicit default-off flag, Google ID-token
verification using the official library, fresh PostgreSQL role lookup and explicit
permissions for every existing admin handler. Mutations recheck current role in
their commit transaction. Trading events and atomic security audit use the verified
actor. Added replayable accounts/audit SQL, with UTC timestamps and schema validation.

Verification runs use locally generated RSA signatures and certificates with the
real Google verifier. No fake verification flag is shipped. The bounded verifier
reuses the existing worker scheduler with a reserved control lane; only public
signing certificates are cached, never account permissions or tokens.

Red evidence: configuration tests initially had **4 failures / 4 passes**, proving
implicit Basic activation and the Google-only password requirement. Identity tests
initially failed collection because their implementation did not exist.

Green evidence:

- **1,110 CLI/config tests** passed, including route-by-route read permissions, malformed/
  expired/wrong-audience tokens, disabled Basic and auth DB failure redaction.
- **219 configuration/model/account-observation tests** passed.
- **7 targeted PostgreSQL tests** passed: repeated migrations, immediate role/active
  changes, subject binding, binding/audit atomic rollback, mutation-time role check,
  verified trading actor/idempotent audit and real service startup/read workflow.
- `python -m hummingbot.cli.swagger --check` passed.

The first broad PostgreSQL attempt was interrupted after source changes. It retained
two fixture check-constraint failures (an unbound invitation incorrectly contained
an issuer) and one legacy service-test setup missing explicit Basic opt-in. Fixtures
were corrected; the targeted rerun above passes. No full PostgreSQL/release acceptance
is claimed for A1. Existing Basic test/load harnesses now opt in explicitly, and
deployment examples explain that the bootstrap method must later be disabled.

## A2 — Whitelist, Google onboarding and audit API

Added `/access/login`, `/access/me`, `/access/bootstrap`, account list/create/change
and `/audit`. Account administration serializes last-admin changes, checks current
permissions in the transaction and preserves subject identity. Google-only mode
requires a bound working administrator; bootstrap is never silently re-enabled.

Audit queries have bounded cursor pagination with microsecond/ID ordering, filter
scope and UTC times. Security changes and their success audit are atomic. Sign-in
and mutation denials retain safe verified actors or an unknown actor; invalid tokens
are not parsed into claimed identities for audit. Ordinary polling/anonymous failures
stay in structured platform logs; failed DB audit writes are explicitly logged with
request IDs, never presented as guaranteed persisted history.

Red evidence: missing account management methods and inaccessible bootstrap route
failed first. A new binding-collision test reproduced a raw unique-constraint error;
Green now rejects a second email for an existing subject with a clear binding code.
The schema tests were updated for the new alternative Google/Bearer security contract.

Verified: **1,087 CLI tests**, **17 focused PostgreSQL checks**, and Swagger generation/
drift checks. Targeted SQL checks cover concurrent self-demotion, rollback on audit
failure, duplicate binding, actor-preserving denials and API restart with disabled
Basic and a still-valid Google token. A separate **real supervisor/API/engine process
restart test passed**: bootstrap first, then a new Google-only revision rejects the
still-populated old password and accepts the existing Google token/account. Offline
tests use signed RSA fixtures through the official verifier, not real Google OAuth
acceptance. Actual external Google sign-in remains an A6 check.

Remaining after A2: A3 encrypted providers; A4 credential lifecycle/automatic cancel-close/
recovery; A5 final FE contract; A6 final-image release/load and external Google/GCP/
Demo acceptance. No managed credential or Disconnect endpoint is enabled yet.


## A3 — Scoped encrypted storage and key providers

Added the idempotent exchange-neutral credential table, AES-256-GCM envelopes with
fresh DEKs/nonces, authenticated scope/account/version/provider metadata, strict
local KEK configuration and Cloud KMS encrypt/decrypt using official Google Auth
ADC. No credential-management mutation endpoint is enabled in this phase, and no
existing environment credentials are imported or modified.

Red/Green caught two defects in the new code: relabeling a KMS key version within
the same CryptoKey was not initially authenticated by the payload; transient Google
credential-refresh errors were initially reported as permanent IAM denial. Both
now have passing regressions. Wrong-key/scope/ciphertext attempts return safe errors,
never partial credentials, plaintext SDK errors or an automatic local fallback.

The full protobuf KMS SDK was tried and rejected: its dependency version conflicted
with an installed connector, and even a compatible SDK version collided with Google
protobuf descriptors loaded by the connector registry. Cloud KMS REST avoids that
shared-process collision. It retains bounded AuthorizedSession/ADC calls and CRC32C
request/response checks; no extra protobuf/grpc dependency remains. The environment's
four pre-existing pip-check conflicts were not changed or declared resolved.

Verified: **34 focused encryption/configuration/HTTP tests**, **1,332 CLI/config/model/
provider tests**, **19 targeted PostgreSQL tests** (including full Google-only process
restart), then **2 credential PostgreSQL tests** after adding scope/constraint checks.
Database row exports contain none of the supplied canary API key/secret/passphrase;
revoked envelopes become SQL NULL while account/version metadata remain. Repeated
migrations preserve encrypted data, and a fresh cipher instance decrypts it after
restart. No managed secret reaches generic command serialization because that API
is not enabled; A4 must prove its dedicated mutation path before enabling it.

The user confirmed no Google OAuth client/GCP KMS resource is available yet. Real
Google/GCP acceptance stays open at A6; offline signature/crypto tests are not a
substitute. A4a is next, followed by A4b automatic cleanup, A5 and A6. The running
Demo service and its database/credentials remain untouched.


## A4a — Managed Set/load, typed receipts and safe replacement

Added secret-input-only Set, safe catalogue/status and dedicated credential-operation
reads on the existing aiohttp API. They use the existing control row, bounded workers,
connector models, PostgreSQL receipts and audit. Environment source rejects mutation.
Set requires acknowledged Stop and no unresolved runtime/mutation/HALT; a fresh
read-only OKX request verifies request UID, permissions and account mode before commit.
Site/routing remain deployment-owned. Account binding survives credential removal;
unbound historical ledgers require reviewed cutover rather than silent adoption.

Only the envelope crosses storage. Retry hashes contain approved scope/version/actor
metadata, never secrets or a secret-derived digest. The first committed retry identity
wins. The commit rechecks current role and versions and atomically updates encrypted
credentials, existing control authority, receipt and audit. Ordinary trading command
history/Stop completion cannot consume a credential receipt or supersede its fence.

The engine stops first, cancels/detaches funding and reconciliation readers, replaces
existing Security connector models off-loop and confirms the version loaded. It never
falls back to environment keys or automatically starts a strategy. CLI connection/
doctor/login and idle funding select the managed source explicitly. Reusing the same
loaded version for a later ordinary Start avoids another KMS call. Missing KEK keeps
API/live health available and trading blocked, without an owner restart loop.

Red evidence: missing store/adapter/source/runtime modules failed first. Owner/control
admission tests then demonstrated ordinary Stop could supersede a pending credential
operation before the new guard. A status regression reproduced a historical failed
receipt masking a subsequently recovered connection; the current view now prefers
fresh matching owner/version evidence while retaining that failed receipt in history.
No Disconnect/retry/cleanup-key route is enabled by A4a.

Verified locally:

- **1,316 native CLI/config/adapter/funding tests** passed. Follow-up focused runs
  covered the later owner/freshness and reader-detachment changes (**55**, **27**,
  **24**, then **16** checks in their recorded scopes).
- **33 targeted PostgreSQL checks** passed for credentials/access/roles and the
  Google-only restart foundation. The first native attempt exposed three obsolete
  route-test path placeholders, which were updated; the rerun passed.
- **22 PostgreSQL credential/runtime/projection checks** passed after adding rotation,
  other-venue fixture and freshness tests; **19** Set/store/service checks passed
  after the claim/loading stage was added. **3 final status/full-process checks**
  passed after the recovered-status fix, including actual supervised engine/API
  restart with encrypted persistence and missing-KEK availability.
- Swagger generation/drift and changed-module style/diff checks passed.

Two PostgreSQL attempts retained fixture failures: the public recovery projection
expected its previous exact shape, and a manually simulated owner did not renew its
heartbeat during a loaded-machine test. Fixtures now include the connection-operation
summary and renew the owner heartbeat; fresh/stale/wrong-owner/wrong-version cases
are tested independently. These are not counted as clean passing attempts.

Tests use local cryptography and an explicitly injected read-only exchange response
inside the test process wrapper. No shipping bypass flag exists. Real OAuth/KMS/Demo
cutover, automatic Disconnect and final-image release/load gates remain open.
Next, A4b adds automatic bot-order cancellation, managed perpetual closure,
restart/unknown-outcome recovery and credential removal. The running Demo is untouched.

## A4b — Automatic Disconnect, scoped close authority and recovery

Implemented the separate Disconnect workflow on the existing owner/control row,
TradingCore, native OKX connectors/order tracker/recorder and command/audit tables.
No additional process, service, web framework or exchange HTTP client was added.
Accepted requests fence ordinary bot orders immediately, preserve encrypted keys
while cancelling/readback occurs, compare held LONG/SHORT independently with fresh
account positions, then authorize only exact identified MARKET/CLOSE orders.
Final REST checks recheck current owner/control/credential versions, stage, side,
quantity, payload and short expiry. Normal Stop and key replacement retain positions.

Close-intent scope is persisted with the native order. Identified close fills and
held-position quantity/PnL/known quote fees commit together; duplicate fills cannot
apply twice and distinct fills cannot exceed the admitted close quantity. Recovered
order events retain original strategy/configuration attribution. Lost responses
remain reconciliation work, never permission to repeat a close. The selected bundle
is erased only with fresh terminal/flat/ledger/detachment evidence in one transaction
with completion/audit. Other bundles, spot holdings, history and unrelated HALTs remain.

Added versioned retry and same-account cleanup-key repair, including an existing
companion slot. They keep the original root fence. Newly submitted requests require
current permission; previously accepted cleanup and its final audit retain the
original actor even after role removal. Child receipts now reach a final outcome
with their root; a superseded repair cannot claim its older key was applied.
The child lookup reuses the indexed audit operation link and command PK, streams
bounded batches and avoids per-child actor reads or a JSON-history scan.

Authenticated funding/reconciliation readers, retained shutdown callbacks, old
controller guards and selected Security models are detached. Current managed and
short-lived candidate secrets are redacted even by a formatter created before key
loading; old secret values are not accumulated in that formatter. Matching loaded
bundles tolerate KMS outage for cleanup; cold missing-KEK errors are explicit and
never cause an environment fallback. Strategy-free cleanup still publishes owner
status/heartbeat so administration remains observable.

Verification on this local worktree:

- **1,356 native tests passed** across CLI, client config, models and credential/
  position adapters. Later focused runtime checks passed **30**, then **16** tests;
  the latter includes explicit missing-KEK classification.
- **140 PostgreSQL tests passed** across cleanup, credential HTTP/storage, ownership,
  recorder and coordinator scopes. Earlier broad follow-ups passed **128** and
  **135** tests; these are overlapping scopes, not additive totals.
- Later focused receipt/progress/checkpoint tests passed **25**, then **21** tests,
  including owner takeover at six durable stages and continued accepted cleanup
  after actor disable. These exercise local encryption and PostgreSQL transactions.
- A supervised two-child process test used real TradingCore/clock/native connector
  construction, local encryption and the real coordinator for an empty account,
  then restarted and confirmed credentials stayed removed and trading stayed stopped.
  Only exchange I/O was replaced inside the test wrapper. No shipping bypass flag
  was introduced. Other boundary tests use explicit simulated exchange fixtures;
  they are not real Google/GCP/OKX acceptance.
- Swagger generation/drift and changed Python style checks passed; final phase A6
  still needs the complete final-code/image gates.

Red/Green and development failures retained in `/tmp/hbot-a4b-*.log`: missing retry/
repair methods; child receipt stuck accepted after parent completion; cumulative
close-fill over-admission; missing cold KEK classification; callback retention and
skipped owner publication. Development regression also caught an incorrect streamed
SQL result API call, missing REST-payload attribute handling, outdated explicit
permission test cases and a registered-model synthetic role value. All were fixed
and their affected scopes rerun. These failed attempts are not reported as clean passes.

Safety limits remain deliberate: unknown bot orders, missing recovery tracking,
unverified crash checkpoints, position mismatch and below-minimum residuals retain
credentials and require review. No force-erase/reset API exists. Live/Demo cutover
of the running service is unchanged and still requires its reviewed procedure.
A5 documents FE/operations; A6 release/load and real external resources remain open.

## A5 — FE handoff and operations

Updated the single FE integration guide with the shipped credential endpoints,
strict versions/idempotency, root/child polling, role-aware actions, saved versus
loaded state and the separate Stop/Disconnect confirmation. All suggested copy,
status labels and guidance are English. Recorded remaining counts are explicitly
not exchange-wide proof; unknown outcomes never imply successful disconnection.

The credential runbook documents local/GCP settings, retained KEK/backups, private
Google/gateway headers, environment-only Basic disable, reviewed existing-ledger
cutover, schema-compatible rollback and future-venue capability limits. The plan's
proposed stage/route descriptions now match the implemented OpenAPI fields.
Runtime documentation still uses the existing authenticated development-only ENV
policy. Swagger generation/drift and documentation link/diff checks passed.
A6 full final-code/image verification and unavailable external resources remain open.

### A6 image probe found a native-throttle integration defect

The first Google-only/managed-key image probe (`37c36234…`, source `f98c4711a`)
passed its 60-second diagnostic load window: 20 reads/s, 1 fill/s, p95 157.59 ms,
p99 519.92 ms, no declared window violations. Automatic Disconnect immediately
afterward correctly retained credentials and reported Action required at closing
positions, so the **overall attempt failed** and is not release acceptance.
Evidence: `build/mm-load/20261105-081017-a5bc18`.

A new Red test uses the actual perpetual connector throttler, rather than the
previous unrestricted AsyncMock. It reproduces `AttributeError` before transport:
cleanup requested singular `/market/ticker`, which has no native registered rate
bucket. The fix reuses the connector's existing plural `/market/tickers` endpoint
and strictly selects the requested instrument, preserving Decimal parsing. Unknown
cleanup exceptions now log their safe class at ERROR without exception inputs;
expected dependency/blocking outcomes retain WARNING. No secret or trading gate
is bypassed, and no close order from that failed attempt reached the simulator.
The corrected image must repeat the probe and subsequent release gates.

## A6 — Local final-image acceptance in progress

Runtime image `8cd8052d5ba1851b992862f531f231aaa0637215c0e5236a122cbc01aa287952`
contains the native ticker/throttle fix. Immutable manifest:
`sha256:b3424b541904126a360bcec8e5aab33b687f11af55c00359af37d3af7b5c2235`.
Subsequent commits change tests/docs only.

- Clean post-fix native release: **2,671 tests plus 11 subtests**; clean PostgreSQL
  rerun: **439 passed**. Two additional supervised slow/denied-provider checks and
  two HTTP response-loss/error-correlation checks pass separately (overlapping scopes
  are not added into one inflated total).
- Final-image offline E2E: **17/17 scenarios**, including upgrade/takeover/rollback,
  `build/e2e-offline/20261105-083127-b83ad5/result.json`.
- Corrected native cleanup probe passes, then a second provider-outage probe passes
  with Google-only fixture auth, no environment credential fallback and 100,000 audit
  rows: 20 reads/s, 100 updates/s, 1 fill/s, p99 297.61 ms. All 62 simulated fills/fees
  reconcile; Disconnect closes both perpetual sides, preserves spot, erases selected
  envelopes and survives restart. `build/mm-load/20261105-090816-2b0e09/result.json`.
- Final 600-second retention/startup gate passes: +18.40 MiB RSS against the original
  32 MiB budget, 12,000/12,000 reads, zero errors, p99 693.95 ms, three completed
  controls and no contract-size startup errors. Evidence:
  `build/memory-review/20261105-091131/result.json`.
- Unchanged full-load/sixty-minute-soak acceptance is running on the same candidate.
- Actual Google/GCP resources are unavailable; the operator confirmed local/offline
  continuation. Existing Demo credentials/database have not been cut over or altered.

The consolidated [acceptance report](../release/admin-access-credentials-acceptance.md)
retains failed attempts and separates production defects from test-harness defects.
