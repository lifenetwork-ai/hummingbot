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

## A6 — Earlier candidate evidence (superseded)

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
- Later identity/admission findings required newer images; the earlier evidence above
  is not transferred to the final candidate. See final closeout below.
- Actual Google/GCP resources are unavailable; the operator confirmed local/offline
  continuation. Existing Demo credentials/database have not been cut over or altered.

The consolidated [acceptance report](../release/admin-access-credentials-acceptance.md)
retains failed attempts and separates production defects from test-harness defects.

## A6 — Final local closeout

Final runtime source `3a3011d6f`, image
`1bb946c39830f79044b520b47dd4d324419cf7f0d63271363673e2ce137b57a3`, manifest
`c92d3d6fefabb18e2d4b2cc2ee78a2b6122989f0d115533166763a667945f2cd`.

The final image passed packaged schema/logging, 600-second memory, actual native
managed cancel/close/removal/restart and the unchanged full load/sixty-minute soak
plus all five faults. Native regression passed 2,680 tests plus 11 subtests.
The final related PostgreSQL regression passed 148 tests; the earlier clean full
PG scope passed 442 tests. These scopes are reported separately. All available local
A6 checkboxes are complete; external acceptance remains explicitly open.

Soak: 72,000 primary + 18,000 auxiliary HTTP 200, no unexpected errors, primary
p99 638.82 ms, +28.125 MiB retained growth against 64 MiB. Across all normal windows,
97,800 primary + 24,450 auxiliary reads pass, 4,884 synthetic fills/fees/positions
match, and 163 role cycles/489 expected denials preserve fresh authorization.
The final cleanup probe verifies 62 fills/fees, both perpetual sides flat, spot
preserved, envelopes erased and no resurrection after restart.

Confirmed fixes include native ticker/throttle compatibility, safe log correlation,
fresh-role work isolated from reporting admission, and bounded reader/control
headroom using the existing scheduler. No extra physical worker or DB pool increase
was made. All failed attempts are retained in the consolidated report.

Recovered probes and the historical isolated HTTP timeout remain qualified; there
is no claim of continuous quote uptime or an exclusive historical timeout cause.
Actual Google/GCP and reviewed existing-Demo cutover remain open. No production
or existing Demo change was made. See [acceptance report](../release/admin-access-credentials-acceptance.md)
and [portable evidence](../release/admin-access-credentials-evidence.json).

## A6 follow-up — Managed environment cold restarts

The requested local-only extension passes on unchanged shipping image `1bb946c3…`:
`build/e2e-offline/20261105-175542-5adcab/result.json`, plus 69 related native tests.
Seven cold revisions cover stopped Demo/Live/Demo, persisted running intent blocked
in Live then resumed only in Demo, native spot/perpetual fills with duplicate
notifications, wrong local KEK with admin alive and original-KEK recovery. Both
financial rows and saved ciphertext remain unchanged across blocked cold starts.
Live admission rejects a Demo-bound ledger with `exchange_account_mismatch`;
usable dummy env keys cannot become managed-source fallback.

The standard offline matrix now includes `managed-environment`. This follow-up
changes only tests/docs, not runtime logic or `.env`; it does not rebrand old load
evidence as a new run. Real Google sign-in, real GCP KMS/IAM and separately reviewed
existing-Demo cutover remain open. See the [follow-up report](../release/admin-managed-environment-e2e.md)
and [portable evidence](../release/admin-managed-environment-evidence.json).

## A6 follow-up — Native cleanup, rotation and repair E2E

Completed in order: deterministic simulator failure stimuli (foundation commit
`de9efad34`), both native crash boundaries, A/B rotation, then expired-key cleanup
Retry/repair. Four initial fixture Red checks and one later physical-send witness
Red check are Green. The final related native scope passes 102 tests; previous
64/101-case scopes overlap and are not added into that total.

The final grouped shipping-image run passes all four cases at
`build/e2e-offline/20261105-213142-34b8a4/result.json`. Image `1bb946c3…` and runtime
source are unchanged. Cleanup cases attempt exactly two native closes, including
rejected attempts in the independent oracle; none duplicate/reverse. Five exact
fills/fees, flat perpetual positions, preserved spot/companion credentials, delayed
selected-key erasure and subsequent restart all reconcile. Rotation preserves
three original fills and every position field except checkpoint timestamp; B is
observed in private REST/WS/funding and matched reconciliation after Start.
Expired keys retain Action required; Retry and both repairs use the same root and
control fence, strict versions and actor-correlated audit.

Two new E2E authoring assumptions were corrected: reconciliation before trading
connector initialization, and comparing mutable checkpoint timestamps. They are
not runtime fixes. An earlier complete four-case group predates the stronger
physical-send witness and is retained separately. Standard `--scenario all` now
includes the cases; this targeted group is not a new full release/upgrade/hour gate.
Real Google/GCP and reviewed existing-Demo cutover remain open. See the
[report](../release/admin-managed-lifecycle-e2e.md) and
[evidence](../release/admin-managed-lifecycle-evidence.json).

## A6 follow-up — Partial closes and cleanup database failures

Fixture foundation `509b34c9c` adds one-shot 40% closes with WS loss, live/terminal
remainders and guarded real-PostgreSQL transaction faults. Three partial-fixture
Red cases and two missing-helper PG Red cases become Green. Live partials retain
one physical send until the original remainder settles; cancelled partials get
only a newly verified 60% residual close. Both cases preserve exact fees/spot,
key retention, the original operation and final restart/removal state.

Native old-image attempt `20261106-151728-624919` finds a runtime defect: after
recorder rollback and process replacement/Retry, a stale incomplete-shutdown flag
blocks the remaining close. Six PostgreSQL Red cases reproduce it; fix `16b268737`
retires only that prior-generation process fence after frozen/fresh terminal,
account and position proof. Pending mutations, unresolved orders, same/unknown
previous generations and active runtime remain blocked; unrelated HALTs persist.
Two more negative cases protect the local-intent and missing-approval boundaries.
A separate fixture Red check fixes blank last-trade/fee terminal republication.

Canonical image `89d5e7c6…` matches the runtime module hash. The original DB-fill
case passes, then final new group `20261106-153445-540333` passes four cases; prior
lifecycle group `20261106-153445-e58454` passes four more on the same image. Fill
faults recover using ordinary process replacement and one API Retry; final-audit
faults roll back ciphertext removal and complete once after dependency repair.
No SQL data/HALT repair, persistence-health reset or runtime threshold relaxation
is used. All close attempts, fills/fees and final state are compared independently.

Full native regression passes 2,689 tests plus 11 subtests. Initial full PG run is
451 pass/1 probe timeout at the existing 300 ms deadline; unchanged confirmation
passes452 and isolated probe passes1. The timeout's exclusive cause remains
undetermined. An extra isolated attempt fails before pytest because Docker's port
mapping is briefly empty; the test runner now waits for a bounded valid loopback
mapping, with two Red/Green and four extra negative checks (six focused passes).

Removed ten unused bot image IDs and their old local-registry references, preserving
new candidate, rollback, all running/stopped container references, other projects
and volumes. Build cache remains; Docker image accounting is not physical free-space
proof. Targeted checklist is complete. No new full release/upgrade/hour gate or real
Google/GCP/existing-Demo cutover is claimed. See the
[report](../release/admin-cleanup-fault-e2e.md) and
[evidence](../release/admin-cleanup-fault-evidence.json).

### Additional partial-close crash and invalid-readback coverage — 2026-11-06

Added 25 behavioral cases: 15 runtime unit cases for untrusted/unavailable ticker
reads, owner loss and terminal partial closes; eight PG proof rejection cases;
two shipping-image cases combining committed partial fills, WS loss and SIGKILL.
Related native52/PG68 and both E2E pass on unchanged image `89d5e7c6…`. Each E2E
recovers the same operation under a new owner, sends only the verified residual,
matches six synthetic fills/fees with three physical close attempts, preserves
spot and proves removal plus another stopped restart. No runtime change/defect,
real Google/GCP/Demo interaction or new full/hour gate is claimed. Disposable
containers are removed; no new image was built. See the
[report](../release/admin-cleanup-partial-crash-e2e.md) and
[evidence](../release/admin-cleanup-partial-crash-evidence.json).

### Further cleanup request resilience coverage — 2026-11-06

Ten additional PostgreSQL cases cover actual lost test-HTTP responses after
Disconnect/Retry/Repair commit, four concurrent Retry/Repair races, disabled/read
actors after Repair preparation, and an injected lost final-removal acknowledgement
after actual PG commit. Related native110 and PG102 pass. The first focused run
passed eight with two test-field-name errors (`version` vs `account_version`), fixed
in test code; no runtime defect reproduced or production change needed. The final
commit exception is an explicit coordinator-boundary injection, not a real PG TCP
fault. Existing native-image E2E evidence is preserved, not rerun/inherited as a new
image or hour gate. No existing Demo or external Google/GCP/OKX interaction. Results
are appended to the existing [report](../release/admin-cleanup-partial-crash-e2e.md)
and [evidence](../release/admin-cleanup-partial-crash-evidence.json).

### Cleanup polling efficiency and timeout recovery — 2026-11-06

Confirmed with behavioral Red tests: repeated reads of already terminal orders,
raw status/fill timeouts classified as unexplained failure, independent per-order
awaits exceeding the remaining poll budget, and an immediate extra close read.
The first optimization also fails a new delayed-fill check; keep a final read of
orders that finished early. Final controlled counts fall nine → seven refreshes,
not five. Eleven new cases cover these paths plus cancellation and PG dependency
retry. No role/secret cache, new scheduler or MM tick/quote change is introduced.

Commit `f1a7429f4`, canonical image `8cdadb21…`, matching runtime SHA: final full
native 2,719 + 11 subtests, related PG 104 and all six native-image cleanup/fault
cases pass. Synthetic fills/fees/positions and every physical close attempt match;
spot and companion keys survive, selected removal/restart remains verified. The
PG-fill fault uses existing process replacement/Retry, without SQL ledger repair
or clearing risk fences. The first-version native 2,717 / PG 104 results and the
fake-clock test correction stay separate in the evidence. No new full/hour gate,
real Google/GCP/OKX or existing Demo interaction is claimed. Test containers are
removed; candidate/rollback images and persistent volumes are retained. See the
[report](../performance/admin-cleanup-polling.md) and
[evidence](../performance/admin-cleanup-polling-evidence.json).
