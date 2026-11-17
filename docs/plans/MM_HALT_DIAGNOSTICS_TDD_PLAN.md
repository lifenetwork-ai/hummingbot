# HALT cause, recovery prerequisites and OKX reconnect TDD

Scope approved 2026-11-17: explain the actual persisted Demo HALT, reproduce and
fix demonstrated code faults, publish safe cause metadata, and retain recovery
proofs. No blanket HALT reset, risk change or implicit trading restart.

Latest connected result: revision164 on exact `a153807bf` completes the large
checkpoint Check/Reset API flow and explicit Start. Control95 is acknowledged
running, no HALT, startup/live/ready200. This closes technical recovery on this
Demo artifact; the broader load/soak and production release gates retain their
existing scopes and failures. Detailed connected receipts are recorded below.

Read-only Demo evidence contradicts the initial ownership-only attribution:
the stored manual reason is `A required runtime task failed; inspect service
availability status.` At 2026-11-16 08:16:53 UTC, the OKX perpetual stream task
exits with `ValueError: list.remove(x): x not in list` after WebSocket close1006
and repeated handshake502. At08:16:54, runtime health reports
`connector_task_stopped`. Later ownership loss/restarts retain this earlier stop.

- [x] RED/GREEN reconnect after an established stream fails, followed by failed
  reconnect attempts; do not reuse/remove the previous iteration's socket.
  Preserve cancellation, retry pacing, subscriptions and message delivery.
- [x] Classify ownership failures with safe fixed codes/SQLSTATE without raw
  SQL, DSNs, exception text or changed ownership/write authority.
- [x] Preserve the initial HALT cause/time/revision and specific runtime task
  evidence; distinguish later cleanup state. Unknown legacy dates stay null.
- [x] Add optional cause metadata to status and Recovery APIs; publication stays
  bounded, unknown/private reason text never crosses the API boundary, and
  recovery blockers remain authoritative. Do not broaden recovery from a string
  match without verification of the stopped runtime and durable scope.
- [x] Update generated OpenAPI/FE types, FE integration guidance and targeted
  banner behavior/tests. Show the concrete cause instead of an unconditional
  EMERGENCY label; retain safe legacy fallback and stale-state handling.
- [x] Run affected native/PostgreSQL/API/frontend checks and hooks. Record
  read-only cloud evidence, remaining live recovery prerequisites and limits.
- [ ] Deploy the repaired image, Stop and run the reviewed connected recovery
  proof/confirmation, then separately Start and monitor actual Demo. Current
  Cloud image159 is unchanged and still HALTed; no manual DB reset is applied.

Local validation: broad native Cloud Run regression4,101 +16 subtests; final
affected recovery/ownership/publication PostgreSQL88 cases (earlier five-module
run93 and targeted3 are overlapping, not extra totals); frontend632 with coverage,
lint/typecheck/build, OpenAPI/type synchronization and changed-file hooks pass.
The successful recovery transaction removes current metadata and retains its
normalized origin code in the audit/operation, preventing stale cause reuse.
Legacy/unrecognized reason plus a shutdown-kind flag cannot imply automatic
recovery. No performance qualification or Cloud recovery is inferred from these
local results.

## Revision160 connected recovery follow-up

Revision160 deploys exact commit `e926ee46c`; both cause APIs return
`runtime_task_failed` with unknown legacy time, and `recovery_reason_unsupported`
is gone. An audited Stop changes control92->93 and is acknowledged. The new
Check returns `recovery_scope_limit`; no Reset or Start is issued.

- [x] Trace the actual blocker with SELECT-only inspection:55,876 orders,
  15,169 fills, matching financial/binding/environment hashes and reviewed anchor,
  no pending intents/unapplied journal/nonterminal trackers. One old attempt is
  `rejected` with `OrderFailure`, no venue ID and no pending create.
- [x] RED reproduces rejecting this resolved attempt, falling back to lifetime
  history and hitting the caps. GREEN admits only a coherent rejected create
  with no fill/venue ID; unknown/live states and inconsistent rows remain blocked.
- [x] A read-only candidate snapshot selects `verified_checkpoint` on the
  unchanged Demo ledger, covering14,519 journaled suffix fills. This is local DB
  eligibility, not exchange verification or permission to reset HALT.
- [x] Affected PostgreSQL59 and native93 pass; preserve original RED1/control10.
- [x] Commit/deploy the follow-up and repeat Check/reviewed Reset/Start. The
  workflow completes on revision164; earlier failed attempts remain recorded.

Evidence: `build/halt-recovery-postdeploy-20261117`. The API commands use the
configured Basic admin identity and retain ordinary audit records. This is not a
new Google-login acceptance. No credentials, risk limits or ledger rows are edited.

## Revision161: local scope fixed; venue proof capacity remains open

- [x] Registry confirms exact `aae12bda3`, revision `hummingbot-demo-00161-svn`,
  image `27f1e6a2...`, Ready100%. Control93 remains acknowledged stopped/HALT.
- [x] Fresh Check `e18bffeb-584c-4924-8842-1ac49a90ef34` now passes the local
  checkpoint selection and enters `checking_exchange`; no `recovery_scope_limit`.
  It fails `recovery_exchange_scope_incomplete`; no Reset/Start is issued.
- [x] SELECT-only journal counts explain a sufficient capacity blocker:12,674
  Spot suffix fills need at least127 pages;1,845 perpetual fills need19. Current
  verifier has100 pages per connector at100 rows/page. The aggregate14,519 suffix
  cannot fit the Spot bound. No exact failed page/cursor is exposed by the API,
  so this does not exclude another pagination failure at an earlier boundary.
- [x] Design/TDD resumable, bounded venue verification: preserve fixed end/head,
  ledger/account/owner/control bindings, exact identities/economics/fees, duplicate
  rejection, complete double-scan agreement and final exposure checks. Persist
  safe progress between jobs instead of increasing the page cap or120s job budget.
  Partial/interrupted proof must never expose confirmation or reset permission.
- [x] Qualify >10k per-connector suffix, restart/timeout/cursor-race and stale-proof
  cases locally, then complete connected Check/Recover/Start with full proof on
  revision164. The historical revision161 stop/failure below remains retained.

Read-only DB inspection, API receipts, registry proof and bounded log observations
are in `build/halt-recovery-r161-20261117`. The limits are enforced protection;
this review does not authorize their removal or claim a new MM/soak pass.

## Segmented verifier implementation scope (approved)

- [x] TDD >10k single-connector history with100-page/120s jobs and resumable
  cursor/digest. Two complete scans, account/open-order/exposure verification and
  fresh venue-head guards remain mandatory. Head changes, duplicate native IDs,
  bad economics/original fees or partial pages cannot authorize recovery.
- [x] Persist bounded private progress and per-pass deduplication rows atomically
  under owner/control/actor fencing; use additive migration023. At most250,000
  verified suffix fills per pass, two passes and30min total proof lifetime.
  The unchanged120s job deadline reserves30s for a final read/write boundary;
  a job voluntarily yields by90s or100 fill pages. Review TTL starts only after
  complete proof. No full native payloads or credentials are stored in progress.
- [x] Recreated workers with the same owner resume durable progress; a changed
  owner/control/ledger/account/risk binding invalidates it. GET timeout retries
  the same durable cursor with bounded backoff; no uncertain mutation is retried.
  Incomplete progress never contains a confirmation token or can_recover=true.
- [x] Keep progress/duplicate rows bounded and clean them on success/failure.
  Reset independently revalidates both scans, never reuses a Check as venue proof.
- [x] Expose sanitized pending progress through existing recovery receipts;
  update API/operations docs and run native/PG/migration/service regressions.
- [x] Connected Check/Recover/Start completes on revision164 after migration023
  and the publication-lock fix. No new deployment env is required.

Local qualification (`build/recovery-segmented-20261117`): native157 pass;
PostgreSQL regression68 pass; physical-owner rollback plus supervised API/cold
restart3 pass; final actor/control/owner/risk/expiry fences7 pass (three overlap
the regression scope). The10,001-fill PG scenario completes Check and a separate
fresh Reset over at least three jobs each, preserving all financial rows and
leaving trading stopped. Full native Spot/Perpetual fee/hedge-leg checks pass.

RED worker interruption found a committed-final-page/pass-transition gap; the
resumer now finishes that saved boundary before indexing the next connector.
The existing five-minute confirmation expiry is also enforced at final Reset,
not extended by segmentation: expiry requires another Check. The30-minute/
250k bounds are ceilings, not a guarantee that slow exchange history can finish
before the unchanged review expiry. No live Reset/Start or new soak pass is claimed.

Raw evidence: `build/halt-diagnostics-20261117`. The current production image
does not include these edits. Keep prior resource failures and release gates.

### Revision162 rollout observation — schema gate remains blocked

Cloud Build `95da713d-839e-4464-8c5c-97b2cff5da01` builds target
`9d2c4b53312c5d9ac97dd300bad1c4e39025a42a`, but its deploy step fails on
`hummingbot-demo-00162-zks`: `schema_incompatible`, engine exit78 and failed
startup probes. Registry config confirms that exact commit in image `981b334c...`.
Revision161 keeps100% traffic. `ENABLE_AUTO_MIGRATION=true` is set on162.
A SELECT-only transaction confirms migration023 absent, table
`recovery_proof_fills` absent and the old advisory owner lock still granted.
Stopcontrol93 is acknowledged, but Stop does not terminate the owning process.
The guarded runner defers DDL while that owner exists; schema validation cannot
accept the missing required table. An owner-drained migration/rollout is still
needed before connected recovery can run. No migration bypass, DDL, Reset/Start,
credential or risk write is performed during this observation. The read-only
proxy is closed. Evidence: `build/recovery-segmented-postdeploy-20261117`.

### Owner-drained migration and finalization/publication race

Migration023 is now applied through the guarded runner after manual scaling0
released the old owner. Original automatic min1/max1,2CPU/1GiB and environment
were restored with the immutable `9d2c4b533` image on
`hummingbot-demo-r023-20261117-115009`, Ready100%. Migration comparison preserves
55,876 orders,15,169 fills,3 positions, financial rows and risk state; control93
remains acknowledged stopped/HALT. No direct HALT or financial repair was used.

Check `a5624666-9750-42d7-b37b-ec7122f17f2e` completes292 native pages across
both14,519-fill scans but cannot finish: engine `critical_persistence_failure`
at11:55:53UTC, followed by restart and a changed owner. Its receipt correctly
fails `recovery_owner_changed`; no Reset or Start is submitted. The mechanism
is reproduced locally by delaying the final snapshot while publishing status:
three RED cases fail because checkpoint hashing holds the BotState authority row.

The follow-up reads the bulk checkpoint before taking that row lock. Final
Check/Reset keep financial SHARE locks, then compare fresh actor/owner/control/
risk before commit; Reset acceptance alone never permits repair. Local native157
and PostgreSQL regression66 pass. Seven focused lock/drift tests cover the three
publication paths, authority changes and a financial edit racing request receipt;
six are also in the regression set. Source/deployed recovery remains open until
the new artifact completes a fresh connected proof. Evidence:
`build/recovery-owner-drain-20261117`, `build/recovery-finalization-lock-20261117`.

### Revision164 connected recovery and continuous Demo — passed scoped workflow

CI deploys exact `a153807bfe55248f815c91bea930d8a21eef32af` on
`hummingbot-demo-00164-b8b`, digest
`sha256:0fe5c423dd05423a09b6ca737da27d800934b958bf7a93a64ed86c34b270b0a0`.
The parallel manual build completes later with the same source but a different
digest; its deployment guard stops before mutation after noticing CI's rollout.
Only the actual CI digest above is qualified by the connected observations.

Fresh Check `4fddec02-0d52-40e6-892a-f6265ec2ef6e` verifies both complete
14,519-fill scans over292 pages and finishes `review_required`/can_recovertrue.
Reset `e951b861-8828-45ba-b703-bc8e23d35fca` independently repeats both scans,
finishes `recovered` within the unchanged review TTL and advances the reviewed
journal anchor to88427. No position repair is proposed/applied. Both receipts
retain the same owner/generation556 through the final running observation.

Before Start, SELECT-only comparison confirms all55,876 orders/15,169 fills and
three complete position rows unchanged; protected risk state and pinned
catalogue match exactly. The journal-owner marker changes553->556 to match the
actual physical owner, not a risk reset. Temporary proof rows are cleaned to0.
Configured Basic admin identity supplies normal API audit; this is not a new
Google-auth acceptance. No manual HALT/financial write or limit change is used.

Explicit Start receipt `4f93bb06-37cf-4f80-8b36-ce3c110001e4` succeeds atcontrol95,
session `4f1a91bd-5751-496c-a951-aac40cc872ee`: both markets, fixedfollow_market,
until_stopped and expires_atnull. Clock, strategy and recorder run; all three
health endpoints200. A bounded post-Start observation exceeds3 minutes. Final
counts55,923 orders/15,172 fills show47 new order records and3 new Spot fills.
Spot works2BUY/2SELL; Perpetual works2SELL CLOSE orders, with BUY blocked by
inventory_limit. Do not count that as perpetual two-sided liquidity acceptance.

No new service_failed/child_restart/recovery_blocked is observed in the sampled
revision164 query window. Two child-probe timeout warnings occur during connector
startup (about301ms each request); the same owner remains healthy/running at the
end, without HALT/restart. Recovery INFO rows are not present in the captured
query, so no exact recovery duration is inferred. Longer soak, resource gate C,
KMS/FE and broader release gates are not closed by this scoped run. SQL proxy is
closed; local disposable PG/image removed. Evidence:
`build/recovery-finalization-lock-20261117`.
