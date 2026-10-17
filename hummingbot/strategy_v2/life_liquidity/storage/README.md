# LIFE storage contracts

`contracts.py` defines immutable `StateBinding`, `StateCut`, resource versions,
owner facts, snapshots and commit receipts, plus `StateRepository`,
`OwnershipRepository` and `UnitOfWork` protocols. These describe storage reads
and publication; no returned value authorizes an exchange request or releases a
financial reservation.

## Current implemented scope

**Current status (2026-10-16, base `faff89ee3` plus this delivery):** the configured file service and its
typed application adapters are described in the later sections. The early
checkpoint descriptions below record the narrower foundation at its introduction.
PG1-C1–C4 and PG1.1–PG1.4 are qualified for the declared offline file-service scope.
Signed staging/reservation/retirement/import actually applies the approved
next-session configuration, retaining financial/native/reporting ancestry.
Supported archive/native repair restores only a known signed frozen source.
Final acceptance: **2,343 passed**, zero failures/errors/skips, 22 existing
warnings in 377.64 seconds; changed-file hooks and diff checks pass. See the
[current closure ledger](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-current-closure-ledger).
Configured PostgreSQL financial backend selection remains unsupported; standalone
PG3 repositories and the PG2 schema/pool foundation are described below. O.8 and
provider/deployment/demo/live gates remain open. Import never activates trading.

`file.py` implements these checkpoint protocols with the existing `PolicyState`
file lock, policy comparison, verified memory/disk reads, atomic replace and
fsync behavior. Logical resource updates and an operation deduplication record
publish together in **one** checkpoint. This new explicit aggregate format is an
offline contract fixture/adapter foundation. It does not import or atomically
combine the controller's existing WAL, reservations, accounting and other files.
There is no controller installation or changed live behavior in this increment.
Segmented archive compatibility and full financial command coverage still need
qualification. The explicit spot legacy adapter described below is separate from
this generic aggregate format.

The PostgreSQL selector currently raises `UnsupportedBackend`; it never falls
back to a local file. The target PostgreSQL adapter will use indexed typed rows
and account transactions, not store this aggregate JSON as its financial schema.
Migration infrastructure is separate and documented in
[the migration guide](../../../../migrations/life_liquidity/README.md).

## Original-runtime final send fence

`postgres/quote_transport.py` pins the original repository/runtime/binding/owner.
Both post-arm authorization and the final synchronous HTTP check reject an
unhealthy/closing runtime or a replacement context. Reconnect cannot revive a
captured send capability. Late accepted ACKs still use the original observation
path; reporting/readback never authorizes a resend. This local fence does not
replace PG4 external credential handoff or the remaining qualified source gates.

## Privileged PostgreSQL cashflow approval import

`postgres/approval_import.py` uses a separate migration-owner connection and
explicit public `OperatorVerifier` keys matching the registered immutable
operator policy. Exact command/cut/fact signatures, complete declared scope,
primary capture/anchor/time and physical-COMMIT context/expiry checks gate one
atomic bill approval plus audit. Runtime roles cannot import or forge approvals.
`lookup` and exact replay verify historical signatures without renewing them.

Import grants no send permission and changes no balance, hold, budget or owner.
Provider bill authentication/capture and separate cashflow application remain
required. SQL checks relational/hash consistency under database-owner trust;
cryptographic Ed25519 verification uses the external public keys in the importer
and lookup. Existing privileged baseline imports and independent anchoring still
require their PG0/PG6 gates. Explicit fact-age settings are mandatory; the 1 MiB
encoding ceiling is a format bound. See the
[approval qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-signed-approval-import-qualification-2026-10-17).

## Explicit PostgreSQL reporting application

`postgres/recorder_application.py` supplies `PgRecorderApplication` for explicit
V2 assemblies. It requires the original owner, exact controller/config binding,
registered event sources, synchronous revoke/readiness callbacks and explicit
queue/callback/history/shutdown bounds. A shared account-runtime mutex serializes
report stage/apply pairs; at least two safety pool slots are required so a report
cannot consume the only primary readback slot. Startup repairs original pending
reports and loads immutable reporting views, without recreating executors.

Executor/position removal and cached PnL changes await actual delivery. Changed
child/exposure snapshots stay in memory for a newer report. Unchanged executor
notifications reuse a verified applied receipt. `enqueue_configuration` revokes
rejection/kill synchronously; cold recovery retains the latch. Event enqueue is
reporting only and never restores SDK dedup/tracking caches. Callback/queue
backpressure, unknown delivery or cancellation revoke local admission.

`ExecutorOrchestrator.async_get_stored_executors_by_controller` awaits the proper
backend. PostgreSQL synchronous stored reads deny; file reads stay on their
worker. Configured LIFE controller/service/source selection, authenticated event
listeners and financial-receipt qualification remain unfinished. Reporting
readiness grants no exchange permission. See the
[application qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-explicit-recorder-application-qualification-2026-10-16).

## PostgreSQL exact held-position reporting

`postgres/positions.py` adds sequenced original snapshots and atomic legacy
Position delivery over migration `000019`. Exact signed legs, fees and wire IDs
survive cold readback; native six-decimal rows are diagnostic. SQL validates
registered controller/wire provenance, monotone sequence/time and retained wire
history. Historical replay cannot overwrite a later snapshot or refund money.
Position reads return the current cut from the same primary statement snapshot.
Explicit row/byte limits reject incomplete history; corrupt or extra native rows
require qualified repair. The shared reporting math avoids ambient Decimal
rounding across truncation boundaries. Actual controller/service/recorder wiring
and financial product composition remain unfinished. See the
[position qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-standalone-held-position-qualification-2026-10-16).

## PostgreSQL connector event reporting

`postgres/events.py` stages registered-source event snapshots and atomically
projects their exact order/status/trade/funding/market-state effects with delivery
receipts. Migration `000018` adds immutable effects and current head pointers;
reporting rows never become financial balances or send grants. The adapter requires
explicit row/byte limits and verifies original provenance, economic deduplication,
predecessors and native rows. Unknown legacy rows are never silently adopted.

`connector_history` is diagnostic. Restoring SDK dedup IDs requires a matching
financial application receipt, which the reporting adapter does not supply.
Tiny signed fees retain their original report value even when legacy six-decimal
columns truncate them. Original replay cannot regress later status or resend.
Order events currently require a known captured SPOT wire; SWAP financial/source
composition and the actual recorder application remain open. See the
[event qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-standalone-event-reporting-qualification-2026-10-16).

## PostgreSQL configuration audit

`postgres/configuration.py` adds `PgConfigurationRepository` with explicit
controller, row and byte limits. The immutable registered
`configurations.body.controller` is required; reporting never registers its own
baseline. Initial/revision/sequence/outcome checks bind stage/apply to the original
owner and current cut. SQL delivery appends one owned legacy Controllers row and
its immutable receipt atomically. Cold retries do not append duplicate rows.

Rejected updates and startup/manual kills latch account HALT at staging. An
unchanged report, later false flag or replay cannot clear the latch. Approved
control/activation is still an unfinished independent port. Configuration and
executor streams share pending/integrity barriers, with late accounting allowed
for consistent pending reports. Reads preserve complete bounded audit history.

Migration `000017` adds only private helpers/fixed commands over existing tables.
Approved access provisioning grants scoped command rights; read-only roles have
no write/private-helper access. Configuration DTOs support both backends while
file publication/auditing explicitly rejects PostgreSQL configuration records.
The configured PostgreSQL controller and actual recorder application remain
unsupported. [Qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-standalone-configuration-audit-qualification-2026-10-16).

## PostgreSQL executor reporting outbox

`postgres/recorder.py` implements an explicit `PgRecorderRepository` over a qualified
`PgSpotRepository`. Its constructor requires the controller ID and explicit
`max_rows`/`max_read_bytes`. These bounds are not live defaults. Initial
`ExecutorProjection` and sequenced `ExecutorProgress` commands keep the original
captured config, namespace, account UID and known financial wire provenance.

`stage` publishes an immutable pending report; `apply` atomically writes the
legacy executor projection and its delivery receipt. `project` is a reporting
repair convenience, never an exchange dispatcher. Exact original retries recover
lost COMMIT replies; old delivery cannot overwrite newer status. Initial delivery
never adopts a foreign globally keyed executor row. Reporting Floats do not become
financial authority, and receipts always grant no trading permission.

Pending reports deny new risk while consistent late ACK/fill accounting remains
available. Corrupted report, operation, receipt or legacy projection blocks reads
and financial commits. A single primary statement snapshot covers integrity,
pending completeness, byte/row budgets and returned rows. Oversized reads deny
before the result JSON is aggregated/transferred. Integrity still traverses retained
history; PG8 performance remains unqualified.

Migration `000016` reuses existing tables and the central migration runner. New
fixed command/read rights use approved `provision_access`; runtime has no direct
financial DML or private proof rights. Actual recorder application/orchestrator wiring remains open. This
standalone repository cannot be selected as a configured PostgreSQL service.

[Qualification and remaining scope](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-standalone-executor-outbox-qualification-2026-10-16).

## Explicit PostgreSQL V2 runner journal

`postgres/runner_journal.py` supplies `PgQuoteRunnerJournal` for explicit offline
assembly with `AsyncActionDispatcher`. The configured PostgreSQL financial
service remains unsupported. Required inputs are the existing repository,
original owner, controller ID/config version, a complete qualified runner-facts
collector, a synchronous local permission check, UTC clock and bounded lifetime
`max_actions`; none has an implicit live default.

`publish` freezes actual CreateExecutorAction configs and claims their slots.
Awaited dispatch bookkeeping must return successfully before
`prepare_quote_action` can make one physical financial preparation attempt.
Historical receipts and cold publication retries cannot reconstruct acceptance.
The adapter serializes its commands and retains original IDs/config bytes.
Capacity/stop rejection stays synchronous; it does not launch detached DB tasks.

After any creation invocation, failed/lost acceptance and cancellation revoke the
original local identity but do not automatically retire a durable claim. Explicit
`reconcile` requires fresh complete original runner absence and delegates to the
existing SQL rejection/retirement/financial-closure guards. Preparing/armed/live
orders never lose their holds merely because an executor is absent. Ambiguous
prepare requires exact repository operation readback, with no automatic retry.

This adapter's `submit_executor_spot_order` always raises
`PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED`, preventing OrderExecutor's native
strategy.buy/sell fallback even if a connector is supplied. No connector
submit, sender permit, source authentication, successor grant or service/backend
installation is provided. Actual source/final-send and full product/recovery/
recorder assembly remain PG3/PG4/PG5 work. The bounded registration map does not
evict identities or grant authority after restart; history/scale remain PG8.

[Runner qualification and final test results](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-explicit-v2-runner-journal-qualification-2026-10-16)
record the explicit functional scope. The original journal uses migrations
`000010`–`000013`; captured request arming adds `000014` over existing operations
and financial rows, without a new table.

## Captured PostgreSQL SPOT request arming

`quote_send.py` and `PgSpotRepository.arm_quote_action` retain the exact captured
seven-field OKX cash/post-only JSON body and its SHA-256 in a permanent operation.
The same account transaction changes PREPARED to SEND_UNKNOWN and holds all
original principal, fees and subsidy. Fresh compatible admission follows the
preparation and is checked again at physical COMMIT. Better current economics
may use a larger original subsidy hold; higher requirements deny. No refund,
reference rebase, new claim or transport authority follows.

`PgQuoteRunnerJournal.arm_quote_action` requires this process's own successful
preparation and allows one physical arm attempt. Lost/cancelled/unknown arm needs
exact repository readback; `lookup_quote_arm` never rearms. Its historical receipt
keeps `trading_permission=false`, even after ACK, and generic bookkeeping arms
cannot be adopted. The runner's submit hook remains denied.

Migration `000014` adds a partial operation index and current-cut integrity checks.
Runtime can execute only the fixed command; private arithmetic/wire helpers stay
private and reporting cannot write. Request checksums diagnose inconsistent
retained metadata under the current database-owner trust contract; they are not
independently anchored historical attestations. History cost and PG8/O.8 remain
open. Actual source authentication and bytes through REST/auth/throttling still
need qualification before any configured PostgreSQL sender can be enabled.

[Captured-request arming qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-captured-request-arming-qualification-2026-10-16)
records **460 PostgreSQL**, **432 file/config/contract** and **21 migration**
passing tests, including real process exits/backend terminations and retained
holds. No configured DB, exchange request or deployment was used.

## PostgreSQL signed account and policy source

`PgQuoteAccountSource.create(...)` pins the OKX connector's credential and endpoint,
reads exact immutable admission policy through `quote_admission_policy(binding)`,
and derives `QuoteSendAgeLimits` from that policy. `source.guard(...)` composes
this source with the one-use HTTP boundary. Authenticated UID and order requests
must consume their actual signed request hooks; configured UID shortcuts,
credential/signature/environment changes, clock mismatch and policy drift deny.
UID/auth TTLs, clock skew, pending capacity and collection timeout are required
explicit inputs. TTL and single-flight collection bound repeated UID reads.

The source rechecks credential/signature/UID freshness after arm COMMIT. Timeout,
cancellation and final denial retain the original financial state/holds. Market,
planner/reference/book/fee provenance and complete runner/service/recorder/safety
composition still need qualification. A bare transport guard is not a configured
account-source fallback; PostgreSQL backend selection remains unsupported.

Migration `000015` checks canonical generic/action-bound PREPARE receipts in
current state, historical lookup and COMMIT, preserving existing snapshot ACLs
across automatic upgrades. Private helpers/old entrypoints remain inaccessible
to runtime/reporting. No table, money, owner or implicit policy is added.
Retained-state SQL audits remain; history/CPU/performance gates are still open.

[Account-source qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-signed-account-and-policy-source-qualification-2026-10-16)
records the 517-test full PG and 542-test file/REST regressions, then final
22-case migration and 34-case source gates after ACL preservation. HTTP/market
facts are synthetic; real account/provider and deployment gates remain open.

## PostgreSQL explicit final HTTP boundary

`PgQuoteRequestGuard` is an explicitly assembled adapter over an accepted and
prepared `PgQuoteRunnerJournal` action. `request_send_guard` carries its async
arm and final synchronous fence through the actual OKX/auth/throttle/REST chain.
The immutable snapshot supplies the exact UTF-8 body bytes sent to HTTP;
redirects are disabled. Local stop, source/config age expiry, lost/cancelled arm
and retry cannot recreate the one-use capability. Scoped async ACKs persist
under the original observation owner and are awaited before connector return.
Unknown requests keep holds and require explicit recovery.

`QuoteSendAgeLimits` and the HTTPS order endpoint are required explicit inputs.
A complete source assembly must bind the authenticated UID, credential/endpoint,
runner/planner/reference/book/fee facts, local clock and age limits no looser than
the imported database policy. Headers/auth flags alone do not authenticate an
account. This adapter is not installed by configured PostgreSQL selection; the
runner submit hook still denies native buy/sell fallback. PG4 exclusion/handoff,
full service/recorder/product integration and release gates remain open.

[Transport qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-explicit-final-http-boundary-qualification-2026-10-16)
records 23 actual disposable-PG and 162 REST/OKX/application regression passes.
The actual connector/auth/REST code uses fake HTTP and synthetic source/runner
facts. No configured DB, real exchange request or deployment is qualified.

## PostgreSQL PG2 foundation

**PG3 unprepared dispatch retirement (2026-10-16, base `1393e8e6b`):**
`AbandonQuoteAction`/`PgSpotRepository.abandon_quote_action` explicitly retire a
DISPATCHED action before any financial attempt/hold. Exact capture, original
writer and fresh complete post-dispatch runner absence are mandatory. Retirement
and prepare share the account lock: preparation winning retains the financial
claim; retirement winning blocks any later preparation of that old identity.
Only its slot is reusable, with a new ID. Ordinary dispatch rejection or timeout
cleanup is still denied. No wallet, fee, subsidy or loss is refunded.

`000013` extends existing fixed commands and COMMIT proofs, without new runtime
privileges, tables or defaults. Cold original-operation readback/retry and late
same-writer cleanup never send or reactivate trading. Actual runner retirement,
source authentication, final transport and configured PostgreSQL selection still
need qualification. See [qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-unprepared-dispatch-retirement-qualification-2026-10-16).

**PG3 terminal action reconciliation (2026-10-16, base `38b97e942`):**
`TransitionQuoteAction(PREPARED, RECONCILED)` now requires proven financial
abort/terminal closure, all order holds closed and fresh complete original-runner
absence captured after that closure. Only the quote slot is freed; original
wallet/fee/subsidy/loss state and permanent intent/wire identities do not change.
Cold retry and COMMIT faults retain the original result without refund or rearm.
Original-writer late cleanup may proceed after HALT/expiry; successor retirement
still needs qualified handoff. Admission facts must explicitly label the exit
source kind; the label does not authenticate an independent price.

`000012` adds private proofs and extends existing action commands without new
money/policy tables or runtime privileges. Unprepared DISPATCHED retirement and
actual runner/source/transport assembly remain pending. See
[qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-terminal-action-reconciliation-qualification-2026-10-16).

**Action-bound PG3 financial preparation (2026-10-16, base `f78ae4991`):**
`action_preparation.py` and `PgSpotRepository.prepare_quote_action` bind the
exact captured DISPATCHED LIMIT_MAKER action to atomic principal/fee/subsidy
holds, action PREPARED and its original financial receipt. Imported admission
limits/rules/economics, original writer/lease and fresh caller-qualified sources
are rechecked at entry and physical COMMIT. Generic preparation cannot adopt a
claimed ID/slot. Potential rebates are excluded from anticipated quote profit.

Verified pending actions retain new-risk/readiness barriers while allowing late
ACK/fill/fee accounting for existing orders. Unknown sends, corrupt/underfunded
state and unsupported products remain denied. Exact retry/cold readback creates
no duplicate holds or external send authority. PG PREPARED adds a durable
financial link to native DISPATCHED semantics. Actual source/runner/transport
assembly, terminal action reconciliation, backend selection and release gates
remain pending. See [qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-action-bound-preparation-qualification-2026-10-16).

**Standalone PG3 quote-publication journal (2026-10-16, base `a7bc3ee50`):**
`actions.py` defines immutable publication/runner-fact commands.
`PgSpotRepository.claim_quote_actions`, `transition_quote_action`, `quote_actions`
and `lookup_quote_action` use ACCOUNT transactions, exact captured identity/payload
and permanent operation readback. `000010` reuses typed action rows and adds empty
imported-policy, descriptor and event tables with an account-wide active-slot index.

Dispatch is a bookkeeping observation of caller-qualified runner acceptance; it
creates no executor or send attempt. Rejection requires the original writer,
PROPOSED state, complete fresh post-claim runner scope and no attempt/reservation.
Another process/generation and an extended dispatch lease cannot inherit the claim.
Facts expiring or ownership changing at COMMIT roll back state/event/receipt.
Unknown/dispatched claims persist across restart; no automatic slot cleanup applies.

The atomic action-to-prepare/runner and qualified terminal reconciliation bridge
remains unported. Pending claims still deny prepare/arm/accounting, so this increment
refuses new claims while any order/hold is live. RECONCILED is explicitly denied.
The opaque captured payload cannot authorize a financial candidate or external send.
Current journal reads/proofs still traverse retained history; bounded history and
performance acceptance remain PG8/O.8. Configured PostgreSQL backend selection,
provider/runner authentication and deployment/demo/live gates stay open.
See [journal qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-quote-publication-journal-qualification-2026-10-16).

**Standalone PG3 order-lifecycle checkpoint (2026-10-16, base `69d585f70`):**
`apply_lifecycle`/`lookup_lifecycle` and `confirm_terminal`/`resume_terminal`/
`lookup_terminal` use ACCOUNT atomicity. Migration `000009` adds empty imported
order policy, cancel progress/events and terminal facts, plus budget-window and
reservation-intent indexes. Proven unarmed abort releases holds once; cancel
records intent/counter while retaining money. Terminal reconciles caller-qualified
original order, exact booked fills, complete account scopes and opening capital
before release. Zero-fill unused subsidy releases; filled quote subsidy and
loss spending remain for economic settlement. Permanent wire/operation IDs persist.

COMMIT rechecks generation and freshness. Late safety closure never activates;
cold original-command retry cannot double release or count. `resume_terminal`
only reads an already committed economic core. Caller facts are not provider
authentication, and no external cancel/send runs. Quote actions, successor/shared
products, settlement, provider ingress, scaled history and configured PostgreSQL
backend selection remain open. Final checks pass **240 actual PostgreSQL tests**
(54 lifecycle cases), **21 migration tests** and **279 focused file/contract tests**.
See [lifecycle qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-spot-order-lifecycle-qualification-2026-10-16).

**Standalone PG3 primary-session checkpoint (2026-10-16, base `08a61f192`):**
`PgSpotRepository.tick_session`, `lookup_session_tick` and
`pending_session_ticks` implement the primary-session safety contract with
ACCOUNT atomicity. Migration `000008` adds imported policy/watermark, versioned
progress and immutable events. Pause/expiry and persistent clock rollback retain
money/holds; prepare and arm enforce verified ACTIVE state at entry and COMMIT.
Late current-generation safety observations cannot resume expired/HALT owners.
Original-command lookup/retry resolves uncertain COMMIT without rearm or a saga
phase. Missing imports and unmatched progress mutations fail closed.

An expired switch policy remains TRANSITIONING with OLD_ORDERS_UNRESOLVED.
Successor reconciliation/activation, observation grants and quote actions remain
unported. The newer lifecycle checkpoint above supersedes the earlier
abort/cancel/terminal gap. No activation or transport permission
is created. Timestamp-watermark verification is under the declared DB-owner
trust and does not qualify unobserved clock faults or independent timer fencing.
Final checks pass **186 actual PostgreSQL tests** (31 session cases), **21
migration tests** and **218 focused file/contract tests**. Older generation and
capital lost-reply fixtures now actually reach their intended DB fault boundary.
See [session qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-primary-session-safety-qualification-2026-10-16).
The previous image predates this source; configured PostgreSQL backend selection
and deployment/demo/live eligibility remain open.

**Standalone PG3 capital checkpoint (2026-10-16, base `2d48d70c8`):**
`PgSpotRepository.apply_cashflow`, `lookup_cashflow`, `observe_nav` and `capital`
implement the existing typed cashflow/capital contracts. Migration `000007`
reuses immutable imported bill approvals, cashflows and ledger events, adding
opening-policy, cashflow-provenance/pending and NAV/highwater tables. Current
owner/cut/binding guards and deferred COMMIT validation apply to every command.
Replays retain economic identity; receipts create no send permission.

Causal reconstruction orders transfers before fills at an equal timestamp and
keeps later funding from financing an earlier trade. Deposits are excluded from
performance; withdrawals preserve highwater and starting-inventory attribution.
Gross loss/subsidy spending and existing holds never reset. Wallet truth that
encroaches holds records a funding deficit and HALT. Impossible histories or an
explicit imported replay bound preserve immutable pending source facts/reasons,
retain prior booked money and block new risk; another deposit cannot clear them.
No liability model or automatic recovery is inferred. Recorded capital `current`
does not certify live quote freshness or authorize trading.

Final checks: **155 actual PG**, **21 migration** and **191 focused file/contract
tests** pass, including four real file/PG transfer/NAV parity cases and actual
process/lost-COMMIT faults. See [capital qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-approved-spot-cashflows-and-capital-qualification-2026-10-16).
Approvals/opening facts are privileged preverified imports; runtime does not
verify their cryptographic signatures or authenticate provider bill capture.
Current verification scans retained facts within a declared bound; scalable
history, full portfolio/lifecycle/markout/settlement, pending resolution, outbox
and provider ingress remain unqualified. PostgreSQL backend selection remains
unsupported. The previous Linux artifact predates this increment.

**Local artifact/recorder checkpoint (2026-10-16, base `e588c8ae3`):** full Linux
ARM64 build/import/source checks and standalone legacy recorder compatibility are
qualified. `000006_recorder_projection.sql` creates thirteen empty reporting
tables in `life_recorder`. `SQLConnectionManager` uses the pinned psycopg driver,
explicit schema and a bounded synchronous pool; startup validates migrated
tables/constraints/indexes/version without DDL, version stamping, FK removal or
SQLite migration. A normalized URL participates in singleton cache identity,
preventing cached SQLite reuse when the configured backend changes. Actual TLS
checks deny untrusted/wrong peers. Financial schema/role/authority remain separate.

Final checks: **121 actual PG tests**, **21 migration tests**, **169 SQLite/file
regression tests** and **13 recorder cases inside the actual Linux artifact**
pass. See [PG2 local qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg2-local-recorder-and-linux-qualification-2026-10-16)
for immutable artifact/source hashes, commands and scope. Full LIFE outbox,
UID/generation provenance, asynchronous scheduling, reporting precision and
recovery remain PG5; the PostgreSQL backend selector is still unsupported. This
does not qualify deployed identities, failure domains, Cloud Run or trading.

`postgres/runtime.py` supplies optional asyncpg primary-only infrastructure.
`PgConfig` requires explicit account/binding/database lineage/principal, pool
capacity and timeout/retry budgets. DSNs are excluded from DTO representations
and driver error messages are sanitized. TLS verifies certificates and hostnames
by default; insecure mode accepts only an explicit literal loopback test URL.
`postgres/runtime_config.py` loads documented `LIFE_PG_*` settings with literal
dotenv parsing; the caller must supply approved identity/binding values. No live
account, balance or lineage is inferred from environment defaults.

`PgRuntime.open()` verifies primary/read-write/durability settings, server major,
migration checksums, schema contract, least-privilege role and complete account
binding; every borrowed connection is rechecked. Command and safety/readback
pools have independent bounded capacity. Waiter cancellation retains physical
work/capacity until completion and closes new-risk admission. Shutdown drains
within its explicit budget; forced termination requires readback.

`allocate(AllocateBalance(...), owner)` is a PG2 **infrastructure primitive**:
account-before-balance locks, full expected owner/binding/version and DB-time
checks, atomic hold/reservation/operation receipt and deferred COMMIT guard.
It creates no intent or send grant and releases no funds. Only definite
deadlock/serialization aborts retry the same immutable command; exchange I/O
never runs inside a retriable transaction. Unknown COMMIT outcomes require
`operation(original_id)` on the primary, retaining holds and admission closure.
`balances()` and `operation()` use reserved safety capacity. Every returned
receipt has `trading_permission=false`.

Numbered migrations create 31 empty typed tables; financial amounts use checked
exact `NUMERIC` (`abs(value)<10^38`, scale at most 18), with no input rounding.
`postgres/access.py` lets the migration owner provision account-scoped access
for pre-existing ordinary LOGIN roles and approved accounts. Runtime has no
direct financial DML/DDL; reporting cannot allocate. No startup routine seeds
accounts, capital, owners or activation grants.

Real PostgreSQL tests run separately from the file suite:

```sh
make life-postgres-test LIFE_PG_TEST_ARGS="--output /tmp/life-pg2-run"
```

The runner creates and removes its own pinned local Docker DB and does not load
the user's `.env`. PG2 qualification: **45 actual DB tests**, **21 migration
tests** and **2,358 expanded file/runtime regression tests** pass. Full financial
ports/parity are PG3; release image/SQLAlchemy/deployed identity and external
durability remain open. The PostgreSQL backend selector still raises
`UnsupportedBackend`; no file emulation or automatic activation is introduced.

## PostgreSQL standalone SPOT preparation (PG3 increment)

`postgres/spot.py` adds `PgSpotRepository` over the PG2 pools/transaction boundary.
It implements the standalone, unfilled preparation portion of the existing
`SpotPreparationRepository` ports. An approved account, immutable session and
explicit `spot_policies` record must already exist. Migration `000004` creates
the tables/functions only; it seeds no policy, capital, owner or activation.
Re-provision existing approved runtime/reporting access through the privileged
setup/import stage to include the new function whitelist.

`snapshot/session/balance/budget`, `attempt/reservation/lookup_prepare`,
`prepare` and `arm` use account-scoped primary state. Preparation atomically
publishes principal, LIFE/USDT fee buffers, subsidy claims in all three windows,
intent and receipt. Persisted inventory/gross/net/budget limits, current owner,
original session/config, exact cut, permanent IDs and occupied slots are checked.
Reads preserve exact decimal strings and use a local precision of 120 digits.
Missing physical principal causes reservation read failure.

Arm commits `SEND_UNKNOWN` before any potential external request, retains all
holds and grants no send permission. Repeat arm, replay after arm and expired
writer admission are denied; primary receipt lookup remains available. Unknown
or unported activity and inconsistent principal/fees/subsidy require recovery.
Stored payload bytes encode the canonical preparation intent; actual protected
transport byte equivalence is still required before backend selection.

The preparation baseline rejects EXIT/shared-risk commands. The observation
extension below adds ACK/fill/fee accounting; release, shared capital,
action/lifecycle/rollback, sender and recorder ports remain pending.
Session metadata retains original deadlines; its derived status/session-start
last-seen baseline is not current connector observation. Cold readback creates
no grant, resend or refund. PostgreSQL backend selection remains unsupported.

Qualification: **76 actual PG tests** (including the previous 45 infrastructure
cases), **21 migration tests** and **92 focused unit/file tests** pass. BUY/SELL
preparation parity, lost COMMIT replies, actual subprocess exits before/after
prepare/arm commits, stale/forged scope, risk/budget limits and decimal precision
are covered. Full PG3 and all deployment/O.8/demo/live gates remain open; see the
[current PG3 checklist](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-standalone-spot-preparation-qualification-2026-10-16).

## PostgreSQL SPOT observations (PG3 increment)

Migration `000005` adds an explicitly approved `spot_fill_policies` record,
immutable fill facts and pending observations, and monotonic accounting progress.
The existing numbered scripts remain unchanged. Runtime and reporting access
still require privileged provisioning for approved accounts; no policy, balance,
owner or activation is automatically seeded.

`PgSpotRepository.acknowledge/apply_fills/lookup_ack/lookup_fill` implement received
standalone SPOT observations. A distinct account-row/current-generation guard
accepts qualified original facts after expiry/HALT in ACTIVE, DRAINING or
RECOVERY_ONLY. Commit checks classify the immutable operation kind; a caller GUC
cannot convert an allocation into a late observation. Preparation remains
ACTIVE/unexpired and cannot clear HALT. Observations use bounded reserved capacity
and do not restore new-risk runtime health.

Complete cumulative snapshots validate original wire/order/side/session, known
trade identity, independent values/skew, quantity and limit prices. New fills,
LIFE/USDT fees, actual assets, consumed principal/fee buffers, gross-loss windows,
subsidy floors and receipt commit together. Rebates credit assets without refunding
gross fee usage or historical loss. Fill-before-ACK remains SEND_UNKNOWN; full
quantity alone releases no unused fee/subsidy or terminal identity.

Fee overruns preserve other holds and HALT new risk. Derived funding deficit is
`max(0,reserved-total)`, not spendable cash. Qualified facts requiring a negative
spot wallet are persisted as pending/RECOVERY_REQUIRED with HALT after provisional
financial writes roll back. Prior assets/holds remain intact, cold restart retains
the barrier and compatible later facts can also be captured. ACK never clears
pending facts. Approved liability/pending resolution and terminal release remain
future work; no debt, refund or successful application is invented.

Qualification: **108 real PostgreSQL tests**, **21 migration tests** and **160
focused unit/file tests** pass. Coverage includes four BUY/SELL × LIFE/USDT fee
parities, gross-cost/rebate/overrun cases, foreign scope, late observations,
generation change at COMMIT, subprocess exits and lost replies before/after
ACK/fill COMMIT, exact cold retry and pending/HALT durability. No provider bridge,
complete cashflow/NAV, shared/product, recorder, terminal/pending resolution or
independent audit/performance gate is qualified. The verifier currently traverses
retained facts; backend selection remains unsupported. See the
[observation checklist](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg3-spot-observation-and-accounting-qualification-2026-10-16).

## Read/write contract (original checkpoint adapter)

- The complete environment/exchange/UID/risk-pool/lineage/schema/policy/config
  binding is required on each read or UoW. UID in a fixture is synthetic, not
  proof of exchange authentication. Policy/config hashes are caller-provided
  bindings; controller integration must verify their actual source.
- `StateCut` contains logical resource versions, revision/event position and a
  logical snapshot digest. Its digest is not a privileged-tamper proof, archive
  verification replacement or send permission. Current views remain subject to
  `PolicyState` verification each time. Cold restore trusts the qualified file
  root under the existing file model; independent audit/replay remains future work.
- Snapshots and input staging are deeply detached and immutable. This generic
  checkpoint payload supports JSON with exact integers and decimal strings;
  floats, `Decimal` objects and live callbacks are rejected. Dedicated financial
  repositories must enforce currency, finite Decimal scale, causal application,
  transition and budget rules; valid generic JSON is not a financial approval.
- `put` stages data without I/O. `commit(operation_id)` checks the fresh cut,
  persisted owner identity/mode and validity interval under the writer lock,
  after queue waits. Clock rollback relative to the previous commit is denied.
  Same operation plus exact expected cut/owner/updates replays without another
  publication; a changed reuse or stale cut fails. Permanent operation identities
  are retained; resource/version and ledger publication are atomic at this scope.
- Only explicit `OFFLINE_SIMULATION` creation is allowed. Restoring missing state
  fails; restore never calls create. Without a supplied synthetic owner, creation
  is `UNARMED`. Owner metadata is a precondition model, not a real grant issuer,
  authenticated process identity, account-wide lock or exchange fencing service.
  PG1/PG4 still need those actual lifecycle services.
- Rollback discards staged work only. Once commit begins, its UoW cannot be reused
  or rolled back. An I/O exception during publication or cancelled commit makes
  the current store uncertain; close it, open fresh objects and read back the
  original operation identity. Never infer a refund, create a new operation/wire
  ID or resend an exchange request from the exception.

## Async and capacity contract

One dedicated worker owns all live `PolicyState` objects. Only immutable command
DTOs, detached initialization bytes and immutable snapshots cross the boundary.
There is no await, external request or user business callback within its file
transaction. An explicit `FileLimits` caps admitted operations, checkpoint bytes
and permanent operation count. Exhaustion refuses new work and preserves prior
state; operation replay/readback remains possible at a full ledger.

Caller cancellation does not kill a worker or release its admission slot before
physical completion. Reads continue to consume their slot until done; cancelled
commits additionally latch uncertainty. `close()` refuses new submissions and
drains admitted work. File-lock and filesystem completion remain host-dependent;
a bounded queue is not a bound on physical disk/flock latency. This is not the
production watchdog/dispatch implementation, Cloud Run shutdown qualification,
or a remedy for O.8 full-history scans.

All capacity values must be supplied; no live defaults are inferred. Tests use
**synthetic** `max_pending=2`, `max_checkpoint_bytes=1 MiB`, `max_operations=16`.
Capacity is recorded in the checkpoint policy except the process-local queue cap.

## Later PG1 settlement, control and runtime seams

`settlement.py` / `settlement_adapter.py` add opt-in, explicitly bound settlement
policy and commands for terminal, fully attributed, physically net-flat spot
inventory cycles. LIFE fees affect asset closure. Per-fill subsidy floors remain;
any additional realized inventory cost belongs to the closing fill. An incomplete
receipt overlays prior campaign/day/session subsidy amounts even after native
settlement. Completion and cold resume do not reset execution-loss budgets,
repeat money writes, change ownership or activate trading.

`control_plane.py` / `control_adapter.py` add Ed25519 approvals for named operator
roles, exact namespace/request/facts/cut and UTC validity. Private signing keys
belong to the operator; the initialized runtime retains only public verifier keys.
Explicit synthetic bootstrap can temporarily sign its own offline seed. Private
operator keys are rejected for non-offline runtime configuration. Persisted seed,
command signatures and owner/approval projections are audited on read. This
protects these signed control records, not the complete arbitrary financial
history or an old exchange credential-bearing process.

New-generation activation requires verified complete account facts, no outstanding
orders and a valid session. Drain revokes local admission before queue waits.
Repeated/cold activation receipt lookup never activates a process. Cashflow
approvals append exact bill/currency/amount/time terms; existing terms cannot be
changed. Signed import can restore a hash-proven original prepare request or adopt
an already approved native cashflow with complete causal projection and no further
money write. Missing historical arm provenance and unknown formats are explicitly
unsupported; broader archived-format/import/policy/handoff services remain open.

`async_dispatch.py` provides explicit bounded create admission/shutdown budgets,
detached identity/deduplication, asynchronous qualification/projection hooks and
fresh local fences after awaits. Stops dispatch immediately, and late storage
replies cannot launch executors after shutdown. A stalled callback cannot extend
the scheduler's declared close budget. The storage worker still owns and drains
its physical admitted work; the scheduler does not promise bounded disk/flock time.

`runtime.py` is an opt-in actual LIFE controller authorization seam. Installation
requires complete `storage_binding`, exact strategy hash and explicit file backend;
PostgreSQL fails before legacy file bootstrap. UID collection and repository calls
are asynchronous, and the existing complete controller/sender checks run on the
bounded owning worker. Local owner generation, expiry, stop, config and payload
fences are independent of the repository reply. Action qualification caches are
bounded and cleared on rejection/dispatch. Protected spot/SWAP requests await fresh
authority after throttling, then run the original local check. The production
release method remains false. A runtime owner checkpoint is not account-wide
financial migration or a live permission artifact.

Remaining PG1 work includes the full product/recorder ports, migration of legacy
controller/proof Path/cache access and complete coupled financial/recorder/source
application. The new seams preserve those requirements; they do not close PG1,
PostgreSQL financial authority, O.8, deployment, demo or live eligibility.

## Tests

### Native joint recovery ports

`open_file_joint(store, recovery=..., owner=..., limits=JointLimits(...))` binds
the actual native `JointRecovery` to one existing `FileCheckpointStore` worker.
The explicit offline factory verifies the account/session, complete controller
configuration, native policies and capture bounds. Its public async reconcile
and cancel entry points delegate to the repository; checkpoint sections never
run on the event loop. Collection and cancellation transport remain asynchronous.
Admission bounds also cover callers waiting for the recovery/cancel locks.

`state(binding)` returns an immutable diagnostic view; `swap_state(binding)`
requires a fresh complete joint receipt and returns detached capital/account
facts and settled signed payments. Capital positions use LIFE, SDK positions use
contracts, margin/collateral/funding use USDT and payment timestamps use
milliseconds. Neither view grants send/rearm permission. Funding and realized
cash affect the wallet once; collateral is not added a second time to NAV.

The native APPLYING/COMMITTED saga remains the financial authority. Partial fills
keep claims; complete terminal evidence qualifies release. Interrupted native
cash/WAL/capital/completion checkpoints replay without another fee or wire send.
Cold startup and expiry disable native send eligibility. Caller cancellation
latches uncertainty and retains the physical worker until completion; a late
result cannot restore health/rearm. Native unsigned rearm is refused.

The default factory rejects `LegacySpotStore` and a separate history worker.
Explicit `couple_cash=True` instead requires the actual `LegacySpotStore` wallet,
WAL and execution-loss ledger, installed accounting/recovery policies and the
same native recovery authority. Qualified SWAP trade PnL/fees and signed funding
publish `JointCashSpot` provenance through an APPLYING/APPLIED storage saga.
The cumulative history preserves source identities and updates that wallet once;
these settlements are internal earnings/costs, never external capital transfers.
They invalidate the prior NAV and retain execution-loss accounting. Immutable
original command/cut/writer and flat native before images allow cold recovery
after wallet, loss or receipt interruption. Unknown formats fail before money;
archived-format reconstruction remains an import gate. No cash receipt rearms.

Cash-only mode rejects SPOT claims before native settlement. Explicit
`couple_cash=True, couple_spot=True` additionally requires a terminal policy and
rejects a competing native fill attributor. Call
`reconcile(binding, owner, spot_fills=(...), spot_facts=...)` with frozen
`FillSpot` snapshots and fresh `SpotFinancialRecoveryFacts`. Source facts must
match the native proof's exact observation time, account/wire/trade/bill scope
and zero spot liabilities. New trades require independent fill-time prices;
historical references and original full fill commands are restored from the
verified journal. A current index price never replaces a missing old price.

`JointCashSpot.spot_fills` extends the accounting saga without changing old
cash-only command encodings. One native wallet checkpoint applies spot fills,
LIFE/USDT fees/rebates and SWAP settlements in causal order (spot before joint
cash at equal time). Loss/fill-floor postconditions precede the final combined
projection. Original flat before images admit only the exact wallet before or
after and known partial loss/floor changes during cold repair. Later activity
can extend a pending batch after repairing its original command. It cannot
rewrite an old fee, price or reference.

Working spot orders retain their unknown-send state and holds; a fill receipt
does not manufacture an ACK. Complete native terminal and explicit account facts
run the store's existing terminal saga before shared-capital release. Multiple
owned orders are accepted only through the complete native proof. Zero-fill
termination releases proven unused subsidy; filled subsidy stays held for its
economic settlement. Wallet/loss/floor/receipt/terminal/capital interruptions
repair on fresh objects without another send or fee. Cold repair never rearms.

SPOT unsent preparations and composition with unrelated pending ACK/fill/cashflow
sagas still require application/readback work; they are not silently adopted.
Controller installation in these tests is explicit isolated fixture assembly,
not a complete configured service factory. SWAP submit/ACK, hedge
child/retry application, controller planning/events and configured startup remain
explicit PG1 work. The tests here establish offline contracts, not provider
authentication, external sender exclusion, performance acceptance or live readiness.

Hedge target updates share read-only native preflight. Invalid versions, pending
children, faults and clock rollback fail before creating a product saga, leaving
the store usable. Existing unrecorded native updates require qualified import.

`recorder_progress.py` adds versioned reporting commands and exact pending
readback. Wire history can grow but cannot shrink; configuration/namespace,
status order and filled-volume order remain binding. Historical repairs use the
latest verified reporting cut, including reconstruction of a missing SQL row.
Legacy SQL Float conversion is compared as reporting representation only.

`FileRecorderApplication.install(repository, runtime, max_pending=...,
shutdown_timeout_s=...)` asynchronously repairs/reads reporting before
`ExecutorOrchestrator(..., repository_reporting={controller_id: application})`
construction. The dispatcher calls the LIFE controller's awaited projection hook
before creating a child. The actual orchestrator then publishes detached progress
through the owning worker and removes a stored child only after its receipt.
Fresh history calls from financial proof code require that worker; callers on the
event loop are rejected. Stop revokes immediately; shutdown drains bounded
reporting work. No reporting receipt releases financial holds or rearms a writer.
`recorder_positions.py` adds sequenced held-position reporting. Frozen snapshots
retain the exact Decimal aggregates and permanent order-ID set; the independent
SQLite summary keeps its existing six-decimal representation. Namespace-derived
row IDs and the verified outbox bind scope without adopting unscoped legacy rows.
Position reports use PROJECTING/APPLIED and compare the complete SQL set before
and after the write. SQLite BEGIN IMMEDIATE excludes competing writers before
the compare read; the entire projection commits in that transaction. Exact cold repair handles missing or already committed
updates; historical lookup reconstructs deleted rows from the latest verified
head. Changed SQL rows, shrinking order history, stale versions and overflow
fail closed. Reporting never qualifies an exchange position or releases funds.

The actual orchestrator captures positions without SQL on the event loop,
retains them until a receipt, preserves any exposure accrued during that wait,
and restores exact aggregates/order IDs from asynchronously loaded snapshots.
Stop revokes immediately even while SQL is blocked. Unsupported initial-position
overrides require qualified import instead of replacing this provenance.
`recorder_events.py` / `recorder_event_application.py` port supported OKX order,
fill, status and SWAP funding reporting. Callbacks capture frozen JSON on the loop;
the worker verifies recorded/native wire provenance and publishes SQL behind a
PROJECTING/APPLIED outbox. BEGIN IMMEDIATE serializes the complete before/after
projection. Exact cold repair handles an absent or already committed transaction;
mixed SQL cuts, altered effects and changed permanent event/trade facts fail.
Connector trade/exchange-ID caches update only after receipt. These observations
never book financial fills, settle funding, release funds or grant authority.
OKX funding timestamps stay in milliseconds; order/fill seconds convert once.
Legacy rows require qualified import; SQL tracking states remain diagnostics,
never an automatic SDK tracker restore. LP and market-data telemetry are outside
this LIFE order/SWAP assembly; no synchronous fallback is installed for them.

`open_file_recorder` in `recorder_factory.py` constructs the actual SQLite manager
and schema on the owning worker, defers legacy constructor history reads and
qualifies all connector UIDs/history before listener installation. Startup blocks
new-risk admission. SQL cleanup is a fixed FIFO worker job before account-lock
handoff; cancelled startup/close callers do not release a physically busy worker.
The factory selects SQLite explicitly and takes a database path and queue/shutdown
budgets; it does not consume credential-bearing DB environment defaults.

After asynchronous reporting setup, the actual headless runner can be constructed:

```python
reporting = await open_file_recorder(
    spot_application.runtime,
    database_path=absolute_sqlite_path,
    config_file_path=config_name,
    strategy_name="v2",
    connectors=(connector,),
    max_pending=report_queue_capacity,
    shutdown_timeout_s=report_shutdown_budget,
)
runner = StrategyV2Base(
    {"okx": connector},
    repository_reporting={controller.config.id: reporting},
    dispatch_max_pending=create_queue_capacity,
    dispatch_shutdown_timeout_s=create_shutdown_budget,
)
# On shutdown, revoke/drain applications before closing their owning store.
await runner.on_stop()
await reporting.close()
await spot_application.close()
await store.close()
```

This explicit preassembled mode reuses bound controllers/provider/action queue and
verified connector identities; it performs no global recorder SQL or YAML reads
in the runner constructor. A separate script config, pending action queue and
unqualified scope/provider/connector changes are rejected. No startup snapshot
reconstructs executors or rearms a writer. Full CLI/config-driven service assembly,
controller config reporting/hot reload and complete product/control application
remain open PG1 requirements.

`recovery_batch.py` adds a typed account recovery saga for pending ACK/fill/bill
commands with later qualified activity. Full source coverage, final causal capital
and the already applied native subset must agree before writes. Original commands,
fees, independent fill/transfer values and spending identities remain binding.
The batch barrier blocks unrelated financial work; cold exact readback and an
explicit superseding batch preserve uncertainty until every native postcondition
is verified. Recovery does not send orders, release reservations, terminalize an
attempt or activate an owner. The spot observer reuses stored values and collects
new values only for new facts. Unsupported preparation/lifecycle combinations
remain explicit recovery gates.

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_storage_contracts.py
```

Tests cover immutable snapshots/staging, binding/owner rejection, stale cuts,
competing UoWs, expiry after staging/queue waits, rollback, cold replay/readback,
ambiguous and cancelled commits, capacity exhaustion, corruption/symlink denial,
unarmed mode, UTC/clock guards and explicit unsupported backend selection. Logs
are temporary; results are recorded in the implementation plan without new
standalone evidence files.

## Spot financial ports and legacy journal adapter

`financial.py` adds typed session, balance, budget, reservation and send-attempt
views plus preparation, ACK and fill repository ports. `PrepareSpot` specifies LIFE quantity,
USDT/LIFE prices, USDT subsidy, **explicit LIFE and USDT fee buffers**, quote slot,
config/session versions and permanently scoped operation/intent/wire identities.
Inputs require finite `Decimal`; float/bool/nonfinite values fail. Decimal text is
canonicalized without quantize/normalize rounding. The representation guard of
64 significant digits and exponent magnitude 64 prevents unbounded formatting;
it is not the future PostgreSQL precision/scale or an OKX tick/lot qualification.
Legacy arithmetic is retained; actual venue/economic calibration remains pending.

`legacy_spot.py` implements these ports on actual `SessionStore`, `IntentWAL`,
`ReservationLedger`, `SubsidyBudgetLedger` and `LossBudgetLedger` files. A separate
`PolicyState` coordinator binds paths, account lock root, UID/environment/lineage,
risk/budget policies and config version. Normal work and lock cleanup use one
bounded worker; one fixed FIFO cleanup slot releases account ownership only after
admitted work finishes, including cancelled close. Live legacy objects and their
locks stay on that worker; callers receive immutable DTOs.

Only explicitly selected `OFFLINE_SIMULATION` may create the coordinator; all
financial journals must already exist. Existing root WAL/reservation and current
session/subsidy/loss formats are qualified by the tests. Archived journal import,
production/demo binding import and full financial compatibility remain open.
Cold open is read-only/recovery-required even if old owner metadata says ACTIVE;
supplying an owner on restore cannot rearm. Unknown WAL attempts close new-risk
admission. The adapter issues no grants and has no HTTP/WS transport.

### Multi-journal preparation

The adapter declares `AtomicScope.RECOVERABLE_SAGA`. A caller requiring a single
account transaction must reject that capability. Generic checkpoint UoW writes
are disabled for this adapter; they cannot rewrite command/hashes behind its API.

```mermaid
flowchart LR
    Check[Fresh binding owner session cut cash and budget checks] --> Start[Persist PREPARING and fee claims]
    Start --> WAL[Persist exact PREPARED WAL and quote slot]
    WAL --> Hold[Persist principal reservation]
    Hold --> Subsidy[Persist subsidy claim]
    Subsidy --> Verify[Verify exact WAL and both holds]
    Verify --> Ready[Persist PREPARED receipt and journal cut]
    Ready --> Arming[Persist ARMING]
    Arming --> Unknown[WAL SEND_UNKNOWN then coordinator receipt]
```

`prepare()` checks native risk limits and available funds including existing/new
fee buffers before any journal write. The coordinator's fee claims reduce
spendable funds; they are retained through preparation/arming and readback, and
actual fee debits consume their corresponding asset buffer. The
native reservation holds principal. Fee claims are keyed by the same permanent
command identity, so replay does not double-allocate them. Budget committed
amounts are separate spending obligations, not cash balances. The caller must
supply fees/subsidy from qualified economics; this storage API does not evaluate
fair value, maker/exit fees, slippage, stress or profitability by itself. Explicit
zero fee fixtures do not establish zero live fees or support a third fee asset.

Each file commits independently. Interruption can leave an incomplete coordinator,
WAL and/or capital/subsidy hold. A fresh snapshot detects STARTED phases or journal
cut mismatch, returns `recovery_required`, preserves readable actual holds and
blocks prepare/arm. It does not complete partial commands, refund fee/principal/
subsidy, mark an order terminal, or resend. Recovery completion and qualified hold
release require the later reconciliation/control-plane ports. Colocated file
hashes are cross-journal version checks, not a privileged-tamper attestation.

`arm()` requires a completed exact prepare, current session/owner/cut, intact
quote slot, an unfilled full reservation and exact subsidy claim. It persists
`SEND_UNKNOWN` before any future transport could run. Repeating arm is denied;
read back the original attempt/operation instead. Neither prepare nor arm receipt
contains a send permit. Post-throttler revalidation, terminal handling,
cashflows, complete accounting/NAV, qualified release, recorder
integration and actual controller wiring remain later work.

### ACK observations

`AckSpot` binds an observation operation to the original prepare operation,
intent, exact wire ID, LIFE-USDT side, session/epoch and numeric exchange order
ID. `observed_at` is local UTC receipt time, not an exchange sequencing clock.
The 128-digit exchange ID bound is a technical encoding limit, not live OKX
qualification. The pending authenticated connector/reconciliation bridge must
establish the source account and authenticity; constructing this DTO is not proof
of those facts.

`acknowledge()` requires fresh account/binding/cut and the current exact writer
token, complete original holds and an already armed attempt. Exchange order IDs
cannot be reused by a different intent, including retained WAL history. A new
ACK operation must use the current cut. An identical operation replay may use
the original or a fresh cut and performs no write; changed observation/writer
identity conflicts. A duplicate matching ACK under a new operation ID is harmless.
ACK payloads and fingerprints are permanently retained in the coordinator.

The saga persists `ACKING`, writes actual WAL `ACKED`, verifies all journals, then
publishes the completed receipt/cut. A crash, cancellation or ambiguous write
preserves principal, fee and subsidy holds; cold readback shows the actual WAL
state and `RECOVERY_REQUIRED` for an incomplete receipt. Cold open remains
unarmed and cannot complete the saga using an old token. No blind retry or
reservation release occurs.

Same-writer late ACKs can be stored after session/token expiry and in DRAINING
or RECOVERY_ONLY mode. This private observational checkpoint path retains the
exact writer token, rejects UNARMED/replaced writers and clock rollback, and
changes neither ownership nor capital/budget values. Generic checkpoint UoWs
still require ACTIVE and unexpired ownership. An ACK does not clear session
expiry, owner mode or another unknown attempt. External session/journal changes
still require qualified reconciliation; this slice does not bypass a mismatched
cut to capture arbitrary observations. Qualified terminal and exact pending
recovery ports are described below; transport authentication remains bridge work. ACK after an
already applied fill validates the partially filled reservation and retained
subsidy claim, rather than requiring an unfilled hold.

### Complete fill snapshots and fees

`SpotFillRepository` uses `FillSpot` and immutable `SpotFillObservation` rows.
Each command carries a complete cumulative fill/fee snapshot for the exact
account-bound prepare/intent/wire/exchange-order/session identity. Signed fees
must be finite Decimal in LIFE or USDT: negative is a debit, positive is an
actual rebate. Known trade identities, fee terms and independent valuations
cannot be changed; every previous fill must remain in later snapshots. A changed
operation fingerprint conflicts; identical replay does not apply money twice.

An explicit `SpotFillPolicy(max_reference_skew_ms=...)` is required and bound to
the coordinator. No live reference-skew default is inferred. Independent fill-time
prices/timestamps must fit this policy and precede local receipt time. The
pending connector/valuation bridge must qualify authenticity, independent source
provenance and actual exchange history; the DTO's `source_kind` alone proves none
of these. Existing coordinators cannot silently change/adopt the fill policy.
Histories with native trades and no corresponding storage fill projection require
a separately qualified import, not an empty attribution bootstrap.

The file saga persists FILLING with the complete proposed facts, then invokes
the existing `ReservationLedger.apply_fills_snapshot(..., require_fees=True)`.
It verifies native quantity/fee postconditions, records per-fill execution loss
in the existing loss ledger and cumulative loss in the subsidy fill floor, and
publishes APPLIED only after complete projection checks. Loss uses the existing
independent edge plus actual converted fee semantics; a favorable later fill or
rebate does not erase earlier loss. Loss uses fill UTC day; subsidy retains the
original reservation bucket semantics. Full cashflow-adjusted NAV, starting
inventory attribution, markout, causal account-wide replay and terminal budget
settlement remain separate work.

Actual debits reduce the matching fee buffer exactly once; rebates never replenish
it. The fee/intent pass is linear in retained trades plus commands, rather than
scanning all trades separately for each command. Fees exceeding a buffer are
recorded when the native balance permits, and latch recovery-required new-risk
denial. Unrepresentable/insufficient balances leave the proposed facts and holds
behind FILLING for qualified recovery. These are storage checks, not a guarantee
that economic loss cannot exceed the pre-trade estimate.

Fills may arrive before ACK and after expiry/loss-budget exhaustion. They update
actual accounting without clearing SEND_UNKNOWN, granting send permission or
changing ownership. A later ACK must match the fill's exchange order ID. Full
quantity does not mark WAL/reservations terminal or refund unused fees/subsidy.
Partial/unknown publication, cancelled work and killed processes retain the
account lock through worker completion and expose recovery readback on cold open.
Pending receipts describe proposed cumulative facts; only APPLIED identifies a
completed storage operation. Cold open remains unarmed and does not replay writes.

This optional file adapter adds no SQL financial migration or live controller
integration. Retained journal/projection checks still scan history; O.8 performance
and full file/PostgreSQL equivalence remain open. Native filesystem durability
semantics are retained; process-kill tests do not qualify power-loss durability.

### Approved cashflows and recorded NAV

`capital.py` adds immutable `SpotAccountingPolicy`, `ApprovedSpotCashflow`,
`CashflowSpot`, `ObserveSpotNAV`, capital views/receipts and async capital ports.
The optional accounting policy binds exact opening balances, independent opening
value/time, numeric bill anchor and a fixed tuple of approved transfer identities,
signed amounts, currencies and millisecond timestamps. It requires the explicit
fill policy. No starting capital is inferred from current balances, nor can
restore silently change approvals/opening values. Adding approvals to an existing
policy requires a future qualified policy/import command; no runtime approval
editing service exists in this slice.

`apply_cashflow()` rejects mismatched approved facts before writing. LIFE transfers
require an independent transfer-time value within the bound skew; USDT uses the
explicit quote unit value 1. The caller's bound DTO is not proof of an authenticated
OKX bill, complete pagination/anchor coverage, independent source provenance or
operator approval; those belong to the pending bridge. A proposed CASHFLOWING
operation persists before the actual native ledger transfer, and APPLIED is
published only after exact bill and capital projection checks. Same operation or
bill replays cannot apply money twice. Transfer amounts never reset subsidy/loss
spending or release order/fee holds. Actual withdrawals may underfund existing
holds: the native debit/proposed facts remain, completion becomes unknown and a
healthy spendable snapshot is denied instead of fabricating balances or releases.

Capital projections reuse `CapitalLedger`, merging approved transfers and stored
fills by timestamp, cashflow before fill on an equal timestamp and lexical ID
within kind, matching the existing attributor. This is the existing offline causal
ordering convention, not a venue-established total order. Earlier transfers
cannot borrow funds from a later fill during replay. Existing starting-inventory
consumption/fee and execution-loss semantics are retained. Exact replayed LIFE/
USDT balances and cashflow identities must match native journals; joint funding
events and unmatched legacy financial history require qualified import/other
product ports, not an empty projection bootstrap.

`observe_nav()` records an independently priced NAV, net-cashflow-adjusted NAV,
drawdown, execution loss and starting inventory PnL. It validates fresh price/time,
writer and cut, forbids regressing the latest valuation/receipt clocks, and stores
the measurement plus monotonic highwater in one coordinator checkpoint. Deposits
are excluded from profit and withdrawals cannot reset highwater; restart preserves
observed highwater. Historical prices never observed by this API are not reconstructed.
Fills/transfers invalidate the financial fingerprint of a previous measurement;
`current` denotes matching financial facts only, not live price freshness, health,
ownership, account-history completeness or permission to trade. Old operation
replays return their historical measurement without replacing the latest one.

Late same-writer transfers can be recorded without rearming expiry. Interrupted
operations block preparation/arming/NAV writes and preserve native funds. Cold
objects remain unarmed/read-only, expose completed or pending receipts, and never
automatically finish partial cashflow operations. Process exits after a native
transfer or a committed NAV preserve readback. Controller bill/valuation wiring,
recovery completion, qualified terminal release, markout/shared-capital/recorder
paths, PostgreSQL transactions and production durability/performance remain open.

### Qualified terminal release and exact-operation resume

`terminal.py` defines immutable `SpotTerminalPolicy`, `SpotTerminalFacts`,
`TerminalSpot`, terminal receipts and async confirm/resume/readback ports.
`terminal_adapter.py` implements the legacy saga separately from the main
adapter, on its same owning worker/account lock. The bound policy requires
explicit positive `max_fact_age_ms` and the accounting/fill policies; no live
freshness default is inferred. Existing coordinators need qualified import to
adopt this policy rather than silently receiving release capability.

Qualification facts bind the full account/environment/lineage/policy identity,
exact original intent/wire/exchange order/session/side/quantity/limit, complete
cumulative fills with signed fees and independent values, exact account balances,
zero liabilities, account-wide trade/cashflow identities and complete pending/
runner scope. The target must be absent from pending orders; foreign identities,
another unknown send, missing/changed fills, stale/future observations, mismatched
cash or incomplete scope deny release before writes. Completed native accounting
projections are verified; ending an order does not substitute for accounting.

These are facts asserted by the pending authenticated gateway/bill/runner bridge.
Completeness booleans, UID bindings and caller-provided snapshots do not prove
exchange authentication, history coverage, finality or credential fencing. This
increment is offline storage qualification, not the integration of the actual
controller/gateway. Live eligibility still requires those release gates.

The saga persists TERMINATING and the exact principal remaining before release,
marks exchange-terminal observation in WAL, confirms native reservation terminal,
marks native WAL terminal, settles zero-fill subsidy with independently verified
actual cost 0, then publishes the complete terminal receipt. Native no-op or
ambiguous publication fails closed. Until final receipt publication, the adapter
overlays any already-released native principal using its durable pre-release
quantity and retains fee claims. Thus partially completed releases do not increase
spendable capacity through the storage API. Native files are not an atomic account
transaction and must not be read directly to bypass this coordinator barrier.

After completion, unused principal and fee buffers are released; terminal intent,
wire and exchange identities remain permanent. New orders may reuse the quote
slot only with new identities and fresh normal admission. **Any filled order
retains its subsidy reservation/fill floor and execution-loss spending.** Ending
the quote does not establish inventory/exit economic settlement. Qualified
inventory-cycle/exit settlement remains future work; no generic per-order actual
cost field is accepted to refund a filled quote's economic hold.

`resume_terminal()` can finish exactly one durable TERMINATING operation after a
crash or cancellation, including on a cold read-only store. It requires the
same persisted writer token/mode, binding and original operation/order/cumulative
fill facts, a fresh cut and fresh complete account/order/bill/runner observations.
Recovery observations are recorded separately from the immutable original command.
Unrelated preparation/fill/cashflow or other terminal partial work denies resume.
It never transfers ownership, rearms, allocates capital, sends/cancels an order,
completes a different saga or repairs absent state. An already completed operation
returns exact readback without a second release. Cold-unarmed state and expired/
draining normal-admission guards remain in force after recovery. The single-host
account lock and writer token do not establish external OKX sender exclusion.

The terminal port does not resolve incomplete prepare/arm/ACK/fill/cashflow
operations, change approval/owner policies or settle filled inventory. Exact
fill/cashflow resume is the separate port below; broader recovery/grant/import
contracts stay open. Complete financial/postgres equivalence, power-loss
durability and O.8 performance remain unqualified; retained history checks still
scan file projections. Tests include native no-ops, partial/full/zero fills,
freshness/identity/scope conflicts, cancellation ownership and real process exits
after principal release, subsidy settlement and final coordinator publication.

### Exact fill/cashflow recovery

`recovery.py` adds immutable `SpotFinancialRecoveryPolicy` and fresh account/
bill/runner qualification facts, plus exact `resume_fill`/`resume_cashflow` ports.
The optional policy requires an explicit positive freshness limit and the bound
accounting/fill policies. It cannot be silently enabled or changed on restore.
`recovery_adapter.py` runs on the same owning worker/account lock. No returned
receipt grants sends, ownership transfer, normal admission or reservation release.

Recovery requires an existing durable FILLING/CASHFLOWING operation, its exact
original command fingerprint and writer token, fresh binding/cut and complete
account balances, zero liabilities, trade/cashflow IDs and accepted-order/runner
scope. Fresh facts may arrive hours after the original fill/transfer observation;
the immutable original fill-time valuation/fee/bill/approval facts are not replaced
by a current price. These source facts and completeness flags require the pending
authenticated bridge; they do not authenticate an exchange or prove account-wide
coverage by themselves.

Before writes, recovery builds both the final expected capital projection and
the actually applied native subset of that projection. Native trades/fees/bills,
balances, intent/session/wire/slot identities and remaining principal must match
that subset; extra external activity, foreign identities, changed facts or other
partial commands deny adoption. The final projection must match the fresh account
facts exactly. Thus a mismatched journal hash does not permit arbitrary native
money or identity state to become authoritative.

The pending operation records fresh recovery facts and performs the same native
deduplicated fill/fee, loss/subsidy floor or approved cashflow writes, verifies
postconditions and publishes APPLIED. Normal fill publication now persists the
original WAL attempt state so recovery cannot infer ACKED from a fill or upgrade
SEND_UNKNOWN. Interrupted older coordinators without this field require qualified
import rather than guessed state. Money, actual fees, loss and subsidy charges
already applied are not applied again; missing checkpoints are completed in the
original units and UTC buckets. An already APPLIED operation returns its historical
receipt without another write, not proof that a later account cut is healthy.

Cancellation, failed publication and killed processes preserve pending state and
native amounts; the owning lock is retained until admitted work finishes. Another
failure requires cold readback before retry. Cold resume stays unarmed, including
after expiry or an unknown send. It does not capture a new ACK, create/cancel/
resend an order, refund fees/subsidy, reset budgets, complete another operation,
issue grants or change approved policies. Native states that cannot produce a
healthy spendable snapshot, including an underfunded withdrawal with outstanding
claims, remain blocked for the separately qualified control-plane/import workflow.
Approval changes, general control-plane/import repair and filled economic settlement
remain open. Exact pending preparation/arm completion is described below. Retained
projection/native checks still scan history; this is not an
O.8 performance or PostgreSQL/full-product equivalence milestone.

### Exact pending ACK recovery

`resume_ack()` completes only an existing durable ACKING operation using its
immutable original ACK command and writer. `SpotAckRecoveryFacts` adds fresh order
wire/exchange ID, LIFE-USDT side, quantity, limit price, cumulative fill and exchange
status to the complete account facts above. Identity and terms must match the
prepared order; cumulative quantity must match already accounted native fills.
Unaccounted fills require their separately qualified workflow before ACK recovery.
The original optional recovery freshness policy remains bound to the coordinator.

Before writing, account, native capital, trade/fee/bill, loss/subsidy and retained
hold projections must agree. Unrelated partial operations or foreign native state
deny recovery. Fresh recovery facts are recorded separately from the original
command. Recovery writes the exact WAL ACK, verifies ACKED/exchange ID, then
publishes the coordinator receipt; completed replay returns historical readback
without another write or a new account-health assertion.

LIVE, PARTIALLY_FILLED, FILLED and CANCELED are observation states with validated
cumulative quantities. Even FILLED/CANCELED facts do not terminalize the attempt,
release principal/fee/subsidy claims or settle inventory economics. Cold resume
stays unarmed after completion or expiry. Cancellation retains account exclusion
until the admitted worker finishes; failed writes require cold readback. Tests
cover actual process exits after native ACK and final receipt publication. Source
facts still require the pending authenticated account/order/runner bridge; these
offline tests do not qualify that bridge, power-loss durability or PostgreSQL.

### Exact pending preparation/arm recovery

`resume_prepare()` and `resume_arm()` complete an existing PREPARING/ARMING saga
on the same bounded account worker. New prepare records retain their original
request cut/writer; arm records also retain the original arm cut/writer and
fingerprint. Recovery cannot infer those fields for older partial records: a
qualified import remains required. The immutable command, current owner token,
binding, current cut and fresh complete account facts must all match.

Native WAL, capital and subsidy identity scopes must belong to known commands.
Previously applied fills/fees/bills, balances and other order holds must verify;
foreign activity, unrelated partial commands, acknowledged/canceled/filled target
attempts or changed session identity deny recovery. Missing principal and subsidy
holds require available capital including retained fee claims and the original
UTC subsidy bucket. Existing holds are validated rather than reserved twice.
`verified_intent_ids()` exposes immutable verified subsidy scope for this check.

Preparation completes missing WAL/principal/subsidy checkpoints, verifies holds,
then publishes PREPARED. Arm completion conservatively publishes SEND_UNKNOWN;
an already written native unknown-send checkpoint is retained. An accepted-wire
observation does not infer ACK or grant a send. Native no-ops fail postconditions.
Expiry and cold completion preserve holds without rearm, refund, owner transfer
or external I/O. Completed recovery replay returns historical readback only.

Tests cover BUY/SELL at all preparation and arm checkpoints, original-cut/writer
conflicts, unqualified/foreign facts, expired UTC buckets, old partial formats,
foreign subsidy scope, native no-ops, cancellation/account exclusion and actual
process exits after principal/unknown-send/final receipt writes. These are file
storage contracts; settlement, broader product/control-plane ports and actual
controller/runner/authenticated PostgreSQL integration remain open in PG1/PG2.

### Spot test commands

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_financial_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_ack_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_fill_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_capital_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_terminal_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_financial_recovery.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_ack_recovery.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_prepare_recovery.py
```

Tests exercise the actual journals, BUY/SELL and fee claims, budget limits/spending
readback, slot/wire identity, concurrent/stale commands, expiry/owner/binding,
cancelled preparation and account exclusion, cold rearm denial and kill-process
interruptions after principal reservation and durable unknown-send publication.
All balances, UIDs, policies, clock values and capacity (2 normal submissions,
1 MiB journal/coordinator capacity, 128 coordinator stage IDs plus one cleanup
slot) are explicit synthetic fixtures. They provide no actual exchange proof.

### Product and independent recorder ports

`products.py` defines async shared-capital, carry, hedge-target and NAV/markout
ports over actual native file implementations. `ProductCut` includes both the
bound coordinator cut and independently verified native checkpoints. Commands
use immutable full-namespace ownership/session facts, permanent identities and
an APPLYING/APPLIED recoverable saga. A pending command cannot be automatically
retried or converted to a send grant. Interrupted product-command recovery and
full SWAP/joint/hedge lifecycle application remain unfinished.

`recorder.py` supplies a separate file-backed SQLite outbox against actual
MarketsRecorder. It inserts or verifies configuration/recovery provenance,
checks the independent DB result and supports exact same-writer cold repair.
It never overwrites later reporting progress on replay. Existing floating SQL
reporting fields are not financial authority. It does not yet replace the
orchestrator's complete projection/event stream or authenticated account bridge.

Both adapters use the existing bounded store worker and are opt-in. Controllers
can bind the product repository to the actual installed component identities;
a second native cache is rejected. Pending product/outbox publication denies
runtime authorization. Legacy file installation/proof migration and complete
automatic service assembly remain PG1 work; PostgreSQL remains unsupported here.

### Coupled spot quote application and observations

`spot_application.py` installs the actual LIFE controller/OrderExecutor hook on
the same LegacySpotStore session, WAL and ledgers. The bounded application
prepares and arms through repository commands before protected OKX placement.
An awaited ACK callback publishes the exact exchange identity through the
repository before connector completion. Fresh UID, owner, config, session and
native sender checks run after the REST throttler; a single-use local fence
checks the wire payload immediately before HTTP. Durable facts alone never
authorize a send. Late ACKs remain observational after local revocation.

`spot_observation.py` uses the connector's account-wide fill/bill/order/balance
methods and a read-only bill reader. It requires exact approved cashflows,
signed fees and balance changes, independent valuations, complete pending
order/algo coverage and actual runner wire provenance. It previews the complete
causal capital timeline before applying missing financial commands, then
qualifies terminal release. Any unsupported or conflicting source facts deny
publication/release. HTTP is synthetic in these tests; no real authentication,
history availability or oracle qualification is inferred.

`spot_lifecycle.py` implements proven-unsent abort and cancel-intent/attempt
sagas. Cancel records count/time before transport, with immutable per-intent
retry intervals; exact cold resume cannot increment the attempt again. The
application never sends from a replayed cancel receipt. One cycle cap spans
all requested scopes, including durable orders absent from the process's issued
cache. Cancel ACK and timeout both leave money/budget holds intact. Values are
explicit controller configuration, not implicit live defaults.

This assembly is **quote-only and opt-in**. Shared-capital SPOT requires the
explicit coupled preparation context installed by the configured service below;
inventory/exit routes remain rejected. Session/watchdog and exact pending-command recovery now
use the same coordinator as described below. Recorder assembly, request-budget
ports, complete product routes, backend-neutral runner proof, recovery batches
with newer external activity and bounded retained-history observation remain
PG1 work. Normal production
release remains disabled. Tests explicitly override it only inside isolated
offline fixtures.

### Session, watchdog and control application

`session.py` reuses SessionManager to plan an immutable journal, persists an
APPLYING barrier, verifies native publication and commits APPLIED. Exact pending
readback/resume retains the original writer/cut/target; it cannot adopt unrelated
wallet/budget mutations or rearm a cold process. Pause, resume and expiry leave
financial amounts unchanged. Successor transitions require full fresh account
facts and closed attempts. A separate signed ACTIVATE updates the admitted
session/deadline; session facts and historical activation receipts do not.

The actual controller delegates safety ticks to one bounded async application
task, independently of quote readiness. Native session writes execute on the
storage worker, followed by qualified cancel/reconciliation. Immediate stop
revokes local admission without waiting for that worker. The explicit
reconciliation-age budget bounds cycle transport, while physical file work
retains its slot/lock after cancellation. Unsupported/missing source data closes
admission and preserves unresolved holds.

`pending.py` exposes typed original prepare/arm/ACK/fill/cashflow/lifecycle
commands. `recover_pending()` previews complete bill/fill/wallet facts and uses
the original stored command, including its independent valuation. Exact pending
completion never resends an order or issues admission. Newer activity outside
that original command remains a gate for the future recovery batch service.

The control application prepares API-qualified `ControlSnapshot` facts for an
external signer. Runtime contains public verification keys only. New control
commands require the bound source snapshot, namespace, cut and signature;
historical replay preserves current local admission and cannot reopen it after
revocation. Signed RECOVER after REVOKE may restore only the latest audited
writer's observation identity; ACTIVE admission requires a new signed grant.

### Protected SWAP worker/application

`open_file_swap(joint)` explicitly binds the installed native protected SWAP
sender to the existing joint store worker. `PrepareSwap.from_config(...)` detaches
an exact executor configuration; `prepare`, `arm` and `acknowledge` retain original
command/cut/writer identity, native WAL/action images and shared-capital terms.
`resume_prepare` / `resume_arm` and the exact recorded ACK command repair partial
checkpoints. Cold readback never restores volatile permits, issues a new ACK,
releases a claim, activates a process or resends a request. Unknown outcomes
require fresh objects and exact readback.

For the isolated offline application, construct `FileSwapApplication(repository,
max_pending=..., ack_timeout_s=..., shutdown_timeout_s=...)` with explicit positive
bounds, then await `application.propose(config)` before executor dispatch. The
installed sender delegates its executor submit/authorization hooks to this
application; calling its old synchronous proposal method from the event loop is
rejected. Account UID collection and HTTP remain asynchronous. Native financial,
session, position, carry and risk checks run on the worker, including a fresh
post-throttler check. Clock/risk-epoch callbacks used by the final local fence must
be cache-only. Checked source timestamps bound the final qualification lifetime;
a receipt alone is never a send grant.

Request charging follows a durable one-shot boundary. Async ACK publication
finishes before connector completion; a late original-writer ACK can be recorded
after local stop/expiry. Timeout/cancellation retains an unknown dispatch slot
until a qualified original ACK, while preserving WAL uncertainty and financial
holds. Slot release after qualified joint recovery is not implemented here.
Application caches also have the store's permanent-operation bound. Cold unresolved
wire diagnostics come from a worker-loaded snapshot, even before an application
is installed; the process cache can conservatively overretain issued wires. Factory
cancellation latches uncertainty and revokes admission while the physical worker
retains ownership.

Tests cover original-command cold interruption, duplicate/conflicting identities,
worker-only journal writes, the actual executor hook, the actual REST throttler
with fake HTTP, independent final fences, async ACK failures and timeout capacity.
The production release gate remains disabled. This explicit assembly does not
complete configured controller/service startup, hedge child/retry application,
general cross-family recovery or the PostgreSQL backend. Native history reads and
full byte verification remain; no O.8 performance gate is closed.


## Configured file service and coupled hedge recovery

`service.FileLiquidityService.open(config, connectors=..., sources=...,
market_data_provider=...)` assembles the actual LIFE controller and V2 runner,
SQLite reporting, SPOT storage and optional coupled SWAP/hedge graph. Use
`open_yaml(absolute_path, ...)` for a bounded YAML configuration validated by
`service_config.FileServiceConfig`. The configuration supplies every accounting,
capacity, owner, freshness, ACK/shutdown and enabled-product policy explicitly.
Sources are injected separately: cached clock/risk/permit/admission values,
capital/account observations and asynchronous complete joint collection/cancel
callbacks. No database URL, exchange credential or `.env` value is inferred.

Fresh `create_binding=True` is restricted to `OFFLINE_SIMULATION` and requires
existing native SPOT seed files. Cold startup reconstructs no executor and stays
unarmed. `recover()` repairs original reporting/hedge/SWAP commands, then uses
qualified joint SPOT fills/account facts or the configured SPOT observer. Source
failure, timeout, cancellation and stop close admission; receipt publication does
not reopen it. Original writer and independently valued transfer/fill terms stay
binding. Joint settlement updates the wallet once, excludes external deposits or
withdrawals from profit, and releases unsent zero-fill holds only after the
complete joint proof. Partial hedge fills retry only their remainder without
resetting cost/attempt accounting.

The joint service watchdog serializes recovery/session/cancellation. Runner
stop/dispatch/status callbacks do no native journal reads on the event loop.
Fill/cancel callbacks capture bounded immutable hints and write them on the same
worker; mutated SDK events, queue overflow or failed hint publication cannot
reopen admission. Joint source reconciliation must qualify hints before planning
resumes. Hints never ACK, release claims or book money.
Original-writer late ACK remains observational after local service stop and
never restores joint health. Early SWAP order events are validated against the
bound repository's original command/WAL/action/capital provenance before SQL;
reporting does not wait for an executor progress row to learn its wire. New arm
and reporting commands capture their cut and apply on one worker turn, avoiding
interleaving publications between separate read/write awaits. Original replay
cuts, sequence and owner checks remain mandatory.
Position and child shutdown projections drain sequentially even at capacity one.
`close()` is idempotent: a cancelled caller cannot cancel the shared cleanup or
release the account lock before the physical worker finishes. This is not a
promise that non-cooperative disk I/O finishes within a cloud shutdown deadline.

### Configured shared SPOT, quotes and carry

The joint service installs `shared_spot.SharedSpotPreparation`: combined capital
preflight precedes original PREPARING publication; the original shared claim then
precedes WAL/principal/subsidy publication on the same worker. Optional
`PrepareSpot.risk_epoch` is mandatory on this route and absent from legacy JSON
when unset. Recovery restores only the original admitted hold, validates writer,
risk provenance and matching qualified wallet facts, and never grants a permit.
Qualified joint unsent/terminal proof exclusively releases both hold families.

Optional `FileServiceConfig.quotes: QuoteServiceConfig` supplies the reference
policy/anchor/model, fee group, action capacity and explicit LIFE/USDT fee buffers.
It requires `ServiceSources.quote_snapshot`, `fee_snapshot` and `monotonic_clock`
cache callbacks plus complete configured own-depth and economic policies. The
service creates the actual native planner and fee/reference binding on the
worker. Sources cannot replace native book/sequence/own-order/rules/reference/fee
checks or configured minimum edge, uncertainty or subsidy policy. Admission
computes subsidy from the current economic candidate; configured fee buffers
must cover a positive maker fee in either possible charged currency. No callback
can substitute its arbitrary subsidy amount. Missing history or changed quote
policy on cold startup requires qualified import, not a fresh empty journal.

Typed order-config captures retain the full subclass and explicit UTC timestamp.
Bounded dispatch/rejection callbacks detach payloads on the loop and publish native
quote state on the worker. Final post-throttler qualification rechecks held quote
permits; a pre-preparation config authorization is invalid once WAL exists.
Non-planner admission terms must remain unchanged. Denied or uncertain attempts
retain principal/subsidy and never cause a transport retry.

Optional `carry_policy` and `ServiceSources.carry_observation` install the actual
`CarryMonitor` and typed product repository on the same worker/capital authority.
Independent mark/index/funding/fee-terms/account streams remain mandatory; failed
carry qualification stops planning and does not book settlement cash. Joint
reconciliation remains the only settlement path. Cold startup never rearms.

### Lost prepared receipt and newer joint activity

If the original PREPARING request already has its exact WAL, principal, shared
claim and subsidy, the service defers completion to the qualified joint batch.
`JointCashSpot.spot_prepared_receipts` retains the original request. Original
account/lineage cut, writer and preparation time, risk/slot terms, all native holds
and prior accounted money must agree before the joint APPLYING publication.
Newer funding, SWAP fees and independently valued SPOT fills then share the
existing cash saga. Final receipt publication precedes qualified unsent abort
and shared release. Cold wallet/receipt/abort/subsidy/release repair never repeats
money, sends a request or restores activation. An expired original writer may
complete observations without extending its session. Legacy JSON omits an empty
receipt list, preserving existing fingerprints.

For this cold unsent case, `runtime.pre_send_provenance_complete` checks original
account-bound initial/progress outboxes, the matching actual SQLite row and the
native PREPARED/hold chain on the worker. The initial reporting operation must
finish before the original preparation cut. Initial executor configuration must
match the original request, including price, size, side, slot, controller and UTC
lifetime. Missing or changed reporting provenance denies scope; SQL reporting
amounts never supply financial authority. The complete independent native joint
account/order proof still owns money and release. No volatile executor or sender
permit is restored from a report.

### Configured risk, inventory and signed control

Optional `FileServiceConfig.risk` installs `FileRiskApplication` on the existing
worker. `nav_observation`, `safety_observation` and `monotonic_clock` are explicit
cache-only source callbacks; configured markout also requires
`horizon_observation(trade_id, horizon_ms)`. Independent source, valuation time,
observation time, freshness and cohort terms are checked. The repository remains
the sole wallet/NAV/highwater writer. Actual available cash caps SPOT margin;
joint measurements retain SWAP positions/collateral without double counting.
`risk.local_ready()` reads caches only and closes SPOT/SWAP/runner admission for
known bad/changed/expired data after worker or transport waits. A timestamp-only
refresh cannot extend the old proof. Cold state requires a new signed grant;
a HALT cannot be cleared by activation, diagnostics or recovery.

Optional `FileServiceConfig.control` contains public Ed25519 keys and roles.
Fresh offline bootstrap requires a `control_seed(binding, owner)` startup callback
returning an externally signed seed; the callback and any signing provider are
not retained by the service. `control.snapshot()` stops cached admission, drains
reporting/hints and obtains independently qualified SPOT/joint facts.
`control.apply(command, approval=..., snapshot=...)` supports owner transitions,
cashflow approval, existing preparation-origin/approved-cashflow imports and the
configured inventory-mode command. Sign outside the runtime. Activation requires
a new same-process grant plus fresh native joint proof where enabled; replay has
no volatile rearm effect. Other policy/lineage/archive commands remain unsupported.

Optional `FileServiceConfig.inventory` requires quotes, risk, control and an
explicit `settlement_policy`. Campaign target, benchmark, deadline, child size,
participation, impact and loss limits are fixed. `inventory_observation` supplies
independent volume/market inputs; native quote own-depth/reference/rules/fee
proofs still apply. Signed `InventoryModeCommand` binds the exact policy digest.
The worker plans one bounded child at a time through the actual asynchronous
runner/SPOT application, using existing financial holds and actual accumulated
fills/costs. Partial terminal retries cannot reset target, budget or deadline.
Cold readback restores no child config or send permit. This application does not
install a second attributor, wallet or exit-loss ledger.

For `liquidity_service`,
`await controller.settle_service_inventory(cycle_id, intent_ids)` routes through
`control.settle_inventory()`. Fresh independent account/order/bill/runner scope
and the original terminal physically net-flat cycle determine actual subsidy
cost; callers provide no release amount. Native settlement receipts deduplicate
replay and cold resume, preserving balances and cumulative execution loss.
Unknown publication locks the store until reopen and never grants activation.
Manual `controller.manual_kill_switch()` revokes locally at once, then coalesces
durable HALT/cancellation work on the existing worker. Physical account exclusion
lasts until FIFO store cleanup completes, including after a controller stop.

### Configured recovery probes

`risk.recovery_probe_campaign_base` explicitly opts into the native recovery
probe guard. Its finite LIFE limit must cover `risk.recovery_probe_base`;
`controller.strategy.risk.resume_policy.probe_size_base` and
`stable_data_duration` must match the service probe size and stability interval.
Quotes and their existing reference/economic policies are required. No campaign
limit is inferred when this option is absent; existing risk policy fingerprints
remain unchanged.

During qualified `DEGRADED` recovery, native quotes are clamped to the probe
size and only one active child is permitted. Actual cumulative ledger fills,
including prior non-probe fills in the same campaign, consume capacity; retries,
replayed fills, terminal release, owner changes and cold restart cannot reset it.
A missing/changed probe policy journal fails worker qualification and cold startup.
The guard reads the sole reservation ledger and introduces no financial writer.

Runner cache-only preflight/final checks restrict the degraded allowance to
bounded LIFE SPOT maker orders. Native candidate and held-permit checks still run
on the worker, including after REST throttling. SWAP/hedge and configured inventory
require `NORMAL`; rejecting those routes does not revoke a valid SPOT probe proof.
Existing markout qualification remains required: this route cannot invent samples
or provide the independent markout-bootstrap probe application. HALT, stale data,
changed source/config, cold state and missing activation remain blocking.

### Original missing-hold and arm recovery

The joint service can conservatively restore an original admitted shared claim,
WAL, principal and subsidy hold when a preparation checkpoint is missing. Exact
original writer/cut/fingerprint, policy and actually applied native money remain
mandatory. It may similarly finish original unknown-send arm bookkeeping.
These steps neither account newer money nor complete a receipt/release. A full
fresh independent native joint proof must then qualify all SPOT/SWAP effects and
the existing final accounting/release saga. Partial restoration stays uncertain;
no new permit, resend or volatile child is reconstructed.

### Configured session successors

The SPOT and joint safety loops use the same worker session application. Native
session decisions keep their original UTC start/deadline. A joint expiry first
cancels owned scope, recollects complete SPOT/SWAP/account/runner facts and then
qualifies a market reference. Outstanding native claims/orders and proposed quote
actions block successor publication. A changed cut requires recollection; no old
facts are relabelled with a new cut. Initial safety input/cut/publication stays on
one worker turn, with a stop check after source callbacks return.

Startup completes only exact original pending session decisions before product
and recorder assembly. Before-native/after-native/final-receipt interruptions
retain original writer, facts and target expiry. Neither completion nor readback
supplies activation. The expired primary can observe a verified quiescent native
successor to obtain the fresh cut for a new externally signed owner grant.
Foreign/mutated/unprojected successor state fails validation. The original sender,
shared allocation and runtime grant stay disabled throughout that bridge.

A separate worker cache tracks the successor's observation deadline; it never
extends the original admission deadline. While waiting for a new grant, the
watchdog keeps reconciling/cancelling and durably checkpoints clock/expiry with
`SessionTick(observe_only_successor=True)`. That command rejects gate/reference
assertions and cannot resume an existing pause or clear rollback. Existing JSON
omits the option when false, preserving original fingerprints. It reports
`SESSION_SUCCESSOR_AWAITING_GRANT`; absence of that grant cannot prematurely
expire or irreversibly pause the already qualified successor. Real expiry and
rollback checks remain effective. A new signed grant still runs all existing
owner/session/account checks and native rearm; historical replay cannot rearm.
Cold initiation without an original recorded decision requires explicit
`AuthorizeSessionObservation` with the optional public-key role `OBSERVE_SESSION`.
The command binds `service.control.observer_id` (a new process observation nonce),
original owner/session/epoch, exact collected facts/cut and UTC expiration.
`control.apply()` persists the signature; `await control.advance_successor(id)`
performs at most three fresh observation steps for expiry, NAV and successor.
Incomplete/nonquiescent scope, stop, rollback, changed owner/source/cut and expired
permission deny the transition. Expiration is checked inside the checkpoint lock
before recording a new decision or NAV. Native-before/native-after interruption
can complete only its already recorded original decision after cold restart;
historical approval replay cannot install the volatile observation capability.

Cold risk observation may update the existing independent NAV/highwater but
cannot change money, grant quotes/reduction or clear manual rearm. Drawdown and
margin breaches still HALT. The separate signed ACTIVATE flow remains mandatory;
the observation command never extends the primary owner or sender deadline.

### Separate exit budget and markout bootstrap

Optional `exit` config fixes the target, child size, slippage/freshness and its
separate native budget. `await service.request_exit(id)` collects complete
quiescent account scope before entering reduction mode. Each original EXIT
preparation selects its budget permanently; quote subsidy availability and
inventory-cycle settlement cannot spend or release that pool. Wallet, fees, WAL,
principal and execution-loss accounting still have one writer. Source-qualified
terminal settlement charges actual fill/value/fee loss once. NORMAL or DEGRADED
may qualify reduction independently of MM; HALT cannot. An explicit quiescent
`stop_exit(id)` returns to MM without restoring activation or send permission.

Markout config may opt into paired `probe_size_base` and `probe_campaign_base`.
Only insufficient cohort samples qualify bounded native bootstrap probes;
pending/missing/adverse observations remain blocking. The read-only source uses
the repository's accounted fills/reservations, not another attributor or ledger.
The smaller applicable markout/recovery probe size bounds a quote. Native
reference, own-depth, rules, fees, economics, journals and final held permits
remain required through the actual runner/REST path.

### Repository runner lifecycle

`service.runner` is a concrete `FileServiceRunner`. Controller queues own quote
creation/stopping; ready ticks retain native executor reporting/storage without
calling abstract script proposals. Native Clock start creates no interactive
application or MQTT publisher. Clock stop revokes synchronously; `service.close()`
joins controller/safety/fee/listing tasks and the existing dispatcher, recorder
and physical file worker. Closing must be awaited by the owning host. This is a
library clock lifecycle; the explicit process entrypoint below owns its connector
clock and signal lifecycle.

### Explicit headless process

`make life-run ARGS="..."` runs `python -m
hummingbot.strategy_v2.life_liquidity.storage.headless`. Required arguments are
`--config /absolute/service.yml`, `--factory module:build`, `--tick-seconds`,
`--startup-timeout` and `--shutdown-timeout` (seconds, explicit finite positive
values). These values require deployment calibration; no live values are inferred.
An independent configured `safety_watchdog_interval_ms` is mandatory.

The trusted local factory receives validated `FileServiceConfig` and returns
`HeadlessInputs` from `headless_inputs.py`: explicit sources, a common market data
provider and named connectors using `HeadlessOkxExchange` /
`HeadlessOkxPerpetualDerivative`. Those connector classes retain native Clock
behavior and make asynchronous native network shutdown awaitable. No exchange
key, qualified valuation, account authority or production feed release is created
by the host. Factories must supply qualified adapters and own any additional
source-client resources they construct; the offline fixture factory is test-only.

The existing migration settings are loaded before even importing factory code.
`LIFE_AUTO_MIGRATE=true` awaits the same central runner; errors block construction.
`--env-file` / `--no-env-file` select migration configuration. This process uses
the explicit **file** financial backend; successful metadata migration does not
select or implement PostgreSQL financial authority. Existing financial state is
required by default. `--create-offline-binding` is restricted to explicitly
synthetic `OFFLINE_SIMULATION` and is never live bootstrap or activation.

SIGTERM/SIGINT close admission before awaited work. The host joins controller,
dispatch, reporting and file-worker cleanup, retains native network-check task
handles before Clock teardown and awaits connector shutdown. Repeated network
stops retain no historical task list. Cancellation/timeout cannot certify an
exchange order terminal or release a still-running worker's physical account
lock; shutdown failure is reported and original uncertainty remains. Per-stage
timeouts here do not qualify the later PG7 overall termination-budget gate.
Tests run native Clock and a real subprocess with fake/disconnected OKX sources,
including SIGTERM, cold restart, cancellation and blocked-worker exclusion.

### Newly observed transfers during joint recovery

`joint.JointSpotRecoveryInputs(fills, facts, cashflows=())` is the detached return
type for the configured `sources.joint_spot_recovery` callback. Existing
`(fills, facts)` callbacks remain supported. The joint repository also accepts
`reconcile(binding, owner, spot_fills=..., spot_facts=..., spot_cashflows=...)`.
Transfer proposals require the explicitly coupled SPOT/joint route, complete
account-wide facts and bounded tuple/count/byte inputs. They are observations;
they neither approve transfers nor grant ownership/send permission.

Supply only new transfers as `CashflowSpot` observations. Each bill must already
match configured or signed approval, currency, signed amount and transfer time.
LIFE requires its original independent transfer-time value. The native joint
source and exact account/bill/trade/runner projection must agree before money.
Unapproved/changed bills, missing coverage, liabilities, foreign UID, stale
reference, later-than-snapshot receipt and changed immutable terms are rejected.
The durable proof budget includes retained repository transfers and ACKs.

The verified native joint intent captures these original transfer values before
the financial saga starts. The financial command then captures the original
cut/owner/full cumulative transfer history before publishing native money.
Cold recovery can restore a captured transfer from either original intent,
including a crash between the two publications. Independent fill/reference
observations remain required wherever they have not already been captured.
A direct `JointCashSpot` without native recovery authority cannot admit a new
transfer; exact saga resume can complete only its original stored command.

Tests combine an original pending ACK or FILLING operation with newly observed
approved deposits/withdrawals, later SPOT fills and settled SWAP fees. Native
cash/fill subsets are independently checked rather than adopting changed hashes.
Wallet/loss/floor/receipt/ACK/native-completion interruptions replay without
double accounting or another send. Transfer receipts are audited with the joint
projection, and external flows remain excluded from adjusted NAV. Principal,
remaining fee buffers and shared claims remain held for working orders. Replays
and cold startup remain unarmed.

This qualifies those specific compositions. Arbitrary simultaneous
hold/arm/lifecycle/terminal/settlement combinations, full coupled parity,
production provider authentication, PostgreSQL and performance gates remain open.

### Controller configuration audit and operational reload

`recorder_configuration.py` defines immutable configuration report/state/receipt
ports and the file recorder adapter. A sequenced, permanent outbox binds each
attempt to the complete namespace, original cut/owner, operation ID, candidate
digest, prior active revision and stable outcome/reason. Native
`MarketsRecorder.project_configuration_reports` uses `BEGIN IMMEDIATE` and the
existing `Controllers` table under a namespace-specific ID. SQL is a reporting
projection; its float timestamp never supplies financial time or authority.
Missing verified rows can be rebuilt, while changed/extra/duplicate scoped rows
are rejected. Lost SQL replies are repaired from original outbox commands at
cold startup without applying an old config, resending or rearming.

`configuration_application.FileConfigurationApplication` is installed by the
service before runner construction. Configuration publication and SQL/history
reads run on the existing worker. No second financial writer, SQL migration or
new table is introduced. Existing pre-feature service state without a verified
configuration history is refused with `CONFIG_APPROVED_IMPORT_REQUIRED`;
approved legacy import is still a separate pending contract. Do not reset a
financial state directory to bypass that check.

```python
receipt = await service.reload_config(candidate_service_config, operation_id="operator-config-001")
receipt = await service.reload_yaml(absolute_yaml_path, operation_id="operator-config-002")
head = await service.reporting.repository.configuration(service.store.binding)
```

The only declared operational update is `controller.manual_kill_switch`.
Its `active_revision` is separate from `strategy.config_version`. All other
controller/service settings, financial policies, bindings and strategy hashes
must remain pinned. The financial adapters normalize the existing false kill
default for policy comparison and check kill independently at local send fences.
This preserves the previous default-false policy fingerprints. A new true kill
in startup YAML is recorded; stale false YAML cannot overwrite a recorded kill.
The disabled process may remain alive, but startup/reload never grants trading.

Invalid YAML or unsupported updates preserve the active configuration, record
`CONFIG_LOAD_FAILED`, `CONFIG_VALIDATION_FAILED` or `CONFIG_UPDATE_UNSUPPORTED`,
invalidate cached actions and stop creates before awaits. Invalid input content
is not stored in reporting/error output. A subsequent valid report does not
clear rejection: a new, independently collected and signed ACTIVATE is required.
Historical readback cannot clear a newer rejection or restore an earlier config.
Setting kill false cannot clear its durable latch or native HALT. Nested quote,
duration/reference and financial strategy changes require the separately planned
approved policy/lineage transition; this application rejects them.

`reload_config` / `reload_yaml` admit a bounded owned task before returning an
awaitable. Caller cancellation does not cancel admitted physical publication.
Pending configuration blocks preparation/final send and signed activation;
close joins publication before SQL/store disposal. Capacity failure revokes
process admission and cannot be cleared by another reload. Cold startup still
requires separate activation. Config history/operation capacity remains explicit
and fails closed rather than deleting permanent identities.

The actual controller update/load-failure hooks use this application. The
headless CLI also handles SIGHUP: it rereads the original absolute `--config`
path with the same bounded YAML loader. Repeated signals coalesce to one active
reload and one notification bit. It prints only a stable outcome and
`activation_granted: false`; SIGTERM/SIGINT retain their existing joined cleanup.
An integration subprocess qualifies malformed YAML, later valid YAML and shutdown
with fake/disconnected sources. Actual REST/throttler tests qualify rejection,
kill and changed SQL while a send waits; holds remain and HTTP is not issued.

Current service scope remains an explicit offline file-service assembly. New
approved transfers alongside original ACK/fill recovery are implemented as
described above. C1 controller/source boundary contracts are qualified. Other
simultaneous partial families, broader signed policy/lineage/archive transitions,
approved nested financial changes and complete file-runtime equivalence remain
open in the PG1 closure ledger. Actual runner/REST tests use
explicit synthetic listing/feed releases; production source qualification belongs
to the later environment gates. O.8, PostgreSQL, demo, live and deployment gates
are unchanged.

## Controller application boundary (PG1-C1)

`ControllerApplicationPort` is the public callback/application contract exposed
through `RuntimeAuthorizationRepository.application`. The actual controller uses
it for events, config/reload, settlement, planning, financial gates, watchdog/stop
and status. It does not access a runtime's private service or store. File-specific
installation and proof helpers are isolated in `file_controller.py`; the original
non-repository controller retains its legacy behavior.

Installed repository runtimes require their owning context for native proof,
planning and journal access. This includes markout/probe capacity and legacy
financial telemetry. Loop callers cannot use those helpers as a synchronous
fallback. Unsigned `manual_rearm()` and private legacy async reconciliation are
rejected before touching journals; use the existing signed control application
and repository reconciliation routes. Standalone mechanism fixtures invoke their
native proof checks explicitly through the worker. A new backend must implement
the actual application ports; a fabricated file path is not an adapter.

Headless factories with extra source clients/tasks may return an async context
manager yielding `HeadlessInputs`. The factory owns acquisition and its context
exit must join/close those resources. The host awaits exit after service and
physical-worker cleanup, using the configured shutdown timeout and one owned
cleanup task. Caller cancellation/timeout does not close source clients ahead of
pending worker/ACK/reporting work. Direct/awaited `HeadlessInputs` remain supported
for factories with no additional owned resources; connector cleanup stays owned
by the host. Actual production source qualification and the aggregate deployed
termination budget remain separate gates.

The [C1 route matrix](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c1-route-matrix-and-qualification-2026-10-15)
maps each declared route to executable selectors. Final expanded regression:
**2,087 passed**, zero failures/errors/skips, 25 existing-category warnings in
239.23 seconds. The later C2 qualification is below; C3/C4, PostgreSQL and O.8
performance gates remain open.

## Joint recovery compositions (PG1-C2)

The configured file service supports original cancel bookkeeping, terminal
release and net-flat inventory settlement interrupted while later independently
qualified SPOT fills, SWAP fees/funding or approved LIFE/USDT transfers arrive.
These extend the existing preparation/hold/arm/prepared-receipt/ACK/fill/cashflow
recovery contracts. Recovery retains the original operation IDs and captured
values; it never reconstructs a different abort, terminal or inventory cycle.

`JointCashSpot` optionally carries IDs of existing terminal/settlement operations.
Those IDs do not admit new work or grant authority. `joint_pending.py` verifies
their immutable origins and native partial subsets. A copied admission view
covers only those operations; actual pending receipts and principal/subsidy
overlays remain in force. The qualified joint monetary batch completes before
original terminal/cycle receipts resume against the actual final account facts.
Execution-loss spending is not refunded by release or external transfers.

Original cancel/abort bookkeeping completes on the existing reconciliation
checkpoint under its mutex and failure/cancellation guard. It does not issue
transport or repeat cancel attempts. New unrelated commands cannot create a
second partial saga while recovery is pending. Cold replay can repair the
original terminal/cycle together with its own interrupted joint cash operation,
then extend accounting with newer facts. Changed terminal fills, incomplete
scope, debt, wrong owners, missing valuations and unrelated session/loss/WAL
mutations are rejected. Neither recovery nor readback activates trading.

The [C2 recovery matrix](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c2-recovery-matrix-and-qualification-2026-10-16)
maps pending families, activities, crash cuts and denials to executable selectors.
The final expanded regression passes **2,172 tests**, zero failures/errors/skips,
24 warnings in 277.96 seconds, including 85 added composition cases. One additional
subsidy-hold mutation denial passes separately without a production-code change.
Changed-file hooks pass. C3 approved configuration/policy/lineage/archive transitions and C4
complete file-runtime acceptance remain open; PostgreSQL financial authority,
O.8 performance and provider/demo/live gates are separate.

## Signed next-session configuration revisions (PG1-C3.1)

`financial_configuration.py` defines immutable stage/discard/state/receipt ports.
The separately pinned public-key ACL must grant `CONFIGURE`; existing operators
do not acquire that role automatically. The runtime retains verification keys
only. The current contract accepts the existing declared `apply_update` fields:
session duration, reference lookback and quote spread/size levels, with exactly
the next configuration version. It rejects other policy changes under that role.

Capture a proposal with the configured control application:

```python
command, snapshot = await service.control.propose_financial_configuration(
    {"session": {"duration": "4h"}}, operation_id="next-session-config"
)
# An external operator signs CONFIGURE over command.json(), snapshot.cut and facts.
receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
state = await service.store.financial_configuration(service.store.binding)
```

Proposal capture closes action admission and collects independent account/cut
facts. Signed publication atomically records the pending candidate and drains
the owner. It retains the active controller configuration, original session
deadline, native journals, balances, paid spending, holds and permanent IDs.
Readback and cold startup retain the signed pending revision without applying it
or activating trading. Local status displays the pending handoff without I/O.

`DiscardFinancialConfiguration(operation_id, expected_sequence,
pending_operation_id, owner)` uses the same signed application path. Discard
appends a decision, preserves the original candidate and leaves the owner drained.
Resuming the old configuration needs a separately qualified new ACTIVATE; a
pending candidate blocks activation. Historical replay checks exact original
signature/cut/facts and cannot resurrect a discarded draft or clear a later gate.

The [C3.1 checklist and results](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c31-signed-configuration-staging-and-qualification-2026-10-16)
record 31 new cases, 75 targeted control/config regressions and the final expanded
**2,204-test** regression (zero failures/errors/skips, 25 existing-category warnings
in 348.04 seconds). Changed-file hooks pass. **C3 remains open** for actual use of
the approved revision across policy/lineage and supported archive/import/repair.
Staging is not a policy migration, a financial reset or an activation grant.

## Core configuration handoff source (PG1-C3.2 prerequisite)

After a signed stage, inspect its original core SPOT source with:

```python
source = await service.control.financial_configuration_source()
# source.scope == "CORE_SPOT"; source.unresolved_intents reports open obligations.
# source.checkpoint_bytes and source.journal_images retain the original bytes.
```

The control application revokes local sends, independently collects complete
account facts and captures bounded original bytes on the file worker. It keeps
admission closed. Signed configuration history, namespace, fresh exact cut,
balances/debts, financial projections and incomplete/reporting barriers are
verified. Open principal and economic holds are retained. Capture writes no
financial state; cold capture retains the original deadline, spent budgets,
cashflow-adjusted capital and permanent IDs.

Before using the image in a future approved handoff, revalidate it with fresh
independently collected facts and the current cut:

```python
snapshot = await service.control.snapshot()
refreshed = await service.store.validate_configuration_handoff_source(
    source, facts=snapshot.facts, cut=snapshot.cut
)
```

A changed source cut, discard/revision or byte substitution rejects the old
image. The content digest identifies source bytes; account facts are qualified
separately. Neither a matching digest nor a caller-constructed image authorizes
adopting state. The actual future publisher still needs a fresh publication
fence, distinct signed target/session authorization and cold recovery.

This image covers `LegacySpotFiles` plus coordinator metadata. It excludes
ancillary quote-action/product journals and recorder SQL; JOINT service capture
is explicitly denied. It is not a complete backup or an import format. Source
retirement, target import, actual revision use and activation remain pending
under C3.2. See the [source-capture checkpoint](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c32-core-spot-source-capture-checkpoint-2026-10-16)
for executable selectors and qualification scope.

The new source cases and related control/financial/session/startup regressions
pass **296 tests**, zero failures/errors/skips, six existing Pydantic warnings
in 42.20 seconds. Hooks pass. This targeted run does not close C3.2 or C4.

## Configured source composition (PG1-C3.2 prerequisite)

Capture and revalidate the configured service source with explicit capacities:

```python
# Example offline inspection capacities, not production/live thresholds.
limits = {"max_bytes": 8 * 1024 * 1024, "max_rows": 128}
source = await service.control.financial_configuration_service_source(**limits)
refreshed = await service.control.validate_financial_configuration_service_source(source, **limits)
```

`source.scope == 'FILE_SERVICE_SCOPED'`. Its frozen core, service JSON, ancillary
byte images and scoped recorder image retain the source config and original
namespace/IDs. The bounded worker verifies every declared installed participant:
quote actions, safety/probes, shared capital, requests, SWAP WAL/actions, JOINT,
hedge and carry. Inventory/exit histories and separate exit-budget bytes are
retained in core. Missing/replaced/symlink/corrupt participants and archived
formats deny; archived origins remain C3.3 work. A pending proposal is preserved,
not inferred rejected or refunded.

The recorder source port audits applied reporting provenance and captures eight
supported table families in one explicit SQLite read transaction, with original
primary keys, column schema and row values. It works in SQLite WAL mode, checks
row/byte limits and refuses unknown columns or unproven/missing/conflicting rows.
Unrelated namespaces are excluded. SQL floats, reporting rows and saved tracker
diagnostics remain reporting only. The complete configured capture repeats the
scoped SQL read and rechecks files/root/fact freshness to detect source drift.

Local sends remain revoked. Snapshot collection uses existing independent
account/reconciliation sources; after that snapshot, capture publishes no
financial/configuration/native/SQL state. Cold readback remains unarmed and
cancelled capture retains its physical worker/account exclusion through close.
Content identity and source inspection do not retire the source, grant a target,
import a revision or activate a process. The [configured checkpoint](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c32-configured-source-composition-checkpoint-2026-10-16)
records tests and remaining C3.2/C3.3/C4 requirements. This scoped image is not a
complete database backup.

The two configured-source selectors add **29 cases**. The final expanded PG1
regression passes **2,255 tests**, zero failures/errors/skips, 25 existing-category
warnings in 295.07 seconds; final hooks pass. This includes all storage selectors
and the native financial/session/WAL/product/recorder/runner regressions recorded
in the plan. This source-capture checkpoint precedes the signed freeze below.

### Signed target reservation and reversible source freeze

After staging a revision, call `service.control.propose_configuration_handoff`
with an operation ID, same-account target binding, canonical target SessionRecord
JSON and explicit `max_bytes`/`max_rows`. It returns the immutable command and
independently collected snapshot for external signing. Apply it through
`service.control.apply` using an explicitly configured `HANDOFF` role. Existing
CONFIGURE/ACTIVATE roles do not acquire this permission.

The target has a new lineage/session UUID and next epoch, the candidate config
hash/version and original anchors/model identity. It starts no earlier than the
original expiry and uses the candidate duration. Freeze requires no unresolved
orders, pending proposals/children/events, principal reservations, shared claims,
open derivative positions or funding liability. It preserves SPOT inventory,
spent budgets, economic history, identities and the original session deadline.

The worker fsyncs an immutable bounded runtime source image under
`state_dir/handoff_sources`, rechecks source/fact freshness, then atomically
records the signed freeze. An orphan image cannot authorize anything. Every
queued worker callback is fenced at execution after freeze. Normal service
startup rejects the frozen source before installing a controller or runner.

Cold maintenance uses `open_legacy_spot` with the original binding, policies and
public verifier. `configuration_handoff(binding)` reads the signed head and
`frozen_configuration_source(binding)` verifies its retained image. This is
historical readback, not fresh target import authority. Missing/tampered images
leave the source frozen. `CancelConfigurationHandoff` needs a new HANDOFF approval
and independently collected current facts/cut through the maintenance repository;
the regular frozen-service snapshot flow is unavailable. Cancellation leaves the
pending revision and DRAINING owner, adopts no observed cash differences and
requires separate recovery/discard/activation. Replays never rearm or refreeze a
later-cancelled source. Cancelled waiters/lost publication replies require cold
readback; cleanup retains physical account exclusion until worker completion.

`storage/test_handoff_authorization.py` covers 25 focused cases. The final expanded
PG1 selector passes **2,280 tests**, zero failures/errors/skips, 24 existing-category
warnings in 313.83 seconds; changed-file hooks pass. This prepared freeze is
reversible: final retirement, consumed-grant/cancel exclusion, target
import/cold resume and actual candidate use remain C3.2. Archive/repair remains
C3.3, final acceptance C4 and cloud credential exclusion PG4. PG1 remains open.

### Source-side irreversible consumption

`ConsumeConfigurationHandoff(operation_id, expected_sequence, owner,
handoff_operation_id, target_manifest_digest)` is a separate signed HANDOFF
decision. Use `store.consume_configuration_handoff` with independently collected
fresh facts and the current source cut through the maintenance repository.
The worker verifies the retained artifact, unchanged native components, complete
order/runner/bill scope, exact accounted cash and no debt or accepted wires.
Approval and target expiry are rechecked at physical publication.

`configuration_handoff(binding).consumed` returns the immutable consumption
command. After consumption, cancellation and another consumer are permanently
denied; exact replay never restores admission. Lost publication replies require
cold readback. Original money, budgets, session deadlines and history stay intact.
The manifest digest pins one intended import, but is not validation of the
manifest's contents or a target startup/activation grant. The target manifest,
inherited native/financial/reporting provenance, interrupted target installation
and actual approved configuration use remain C3.2 work.

`storage/test_handoff_consumption.py` adds 16 cases. Final expanded regression:
**2,296 passed**, zero failures/errors/skips, 24 existing-category warnings in
322.06 seconds; changed-file hooks and `git diff --check` pass. Target import and
the C3.3/C4/PG1 parent gates remain open.

## Qualified target import and exact-source repair (PG1 closure)

This section supersedes the source-only remaining lists above. PG1 is qualified
for the declared offline file service; PostgreSQL, O.8, deployment and provider
qualification retain their separate gates.

### Transition sequence

1. Stage the declared duration/reference-lookback/quote revision through the
   existing control application with a CONFIGURE approval. This drains the old
   session without changing its configuration, expiry or financial state.
2. Reserve the exact same-account target binding/session with a distinct HANDOFF
   approval. The persisted source artifact and physical worker fence precede
   source freeze. Outstanding wires, principal/shared claims or derivative
   exposure deny handoff and stay held.
3. Build `TargetImportManifest(operation_id, handoff_operation_id, source_digest,
   target_service_json)` and call `store.validate_configuration_target(manifest)`
   through source maintenance. It validates the staged candidate, reserved
   absolute session/owner, disjoint target directory and unchanged financial,
   product, recorder and verifier policies. Only the approved strategy/version,
   reserved binding/owner and declared relocated native paths may differ.
4. Consume that exact manifest digest with a fresh HANDOFF approval using
   `ConsumeConfigurationHandoff`. This permanently denies cancellation and a
   second consumer. A digest alone does not create an executable target.
5. At the reserved target start, collect fresh complete independent source
   account facts and the current source cut. Construct
   `ImportConfigurationTarget(manifest, source_root_sha256)` from the exact
   retired coordinator bytes. Sign its IMPORT approval in the **target**
   namespace, binding the original source cut/facts. Call
   `store.import_configuration_target(command, approval=..., facts=..., cut=...)`
   on the owning source maintenance worker.
6. Close/join source maintenance before opening `FileLiquidityService` with the
   approved target configuration and independently scoped fresh target sources.
   Startup uses verified import ancestry, not a new seed. The target is
   RECOVERY_ONLY/unarmed. A separate fresh ACTIVATE with a new generation and
   current target facts is mandatory before any quote/send admission.

The importer publishes original native images and immutable `ancestry.json`,
then a hash-addressed `qualification-<sha256>.json`, then `binding.json` last.
Policy-only path/strategy/binding relocation leaves monetary state exact.
Session creation uses the already reserved absolute deadline; previous sessions,
approvals/configuration and all permanent financial/operation identities remain
in sealed ancestry. Paid costs, economic holds and NAV/high-water are retained.
Source signatures/consumption, initial/refreshed import signatures, exact retired
roots and inherited immutable records are audited during cold reads.

An interrupted import may resume only its exact owned files. An expired partial
attempt requires fresh qualification; its initial signed ancestry is never
replaced. An unknown marker reply or cancelled caller requires cold source
readback. The original account lock remains held until physical publication and
cleanup finish. Approval, fact freshness, target expiry and source bytes are
rechecked after the marker's temporary-file fsync, immediately before its link.
Readback never activates or rewrites a running target.

The recorder uses the same scoped SQLite database. Original executor/event
payloads retain their namespace; they cannot be silently relabelled as target
commands. Sealed SQL schemas/rows/primary keys remain in ancestry. Position is
the current reporting head: its sequenced exact before/after sets may replace
superseded physical IDs during a lineage transition. Whole-set SQL CAS prevents
duplicate reporting exposure; original exact snapshots/IDs remain in ancestry.
MarketState remains a mutable SDK diagnostic snapshot and provides no financial
authority. Shutdown before the first clock tick uses the declared service UTC
clock for a restored position's reporting timestamp.

### Cold archive/native maintenance

Use `open_archive_maintenance(config, clock=...)` after closing the source
runtime. This explicit worker acquires the same account lock and opens only an
ordinary verified coordinator plus an already signed frozen
FILE_SERVICE_SCOPED source. It installs no controller, recorder, SDK listener,
sender or activation API. Call `repair_cut()` for the current immutable cut.

`ArchiveRepair(operation_id, handoff_operation_id, source_digest, component,
before_sha256, source_root_sha256, archive_policy_json)` needs a separate IMPORT
approval and fresh complete independent account facts equal to the sealed
financial origin. Supply an explicit native `ArchivePolicy.json()` for archive
normalization. Supported readers cover SPOT/SWAP WAL partitions, reservation
partitions and declared PolicyState participant archives. Native policy,
coverage, bounded segment bytes/counts and hashes must verify; decoded logical
content must equal the original signed image exactly.

For explicit known-image restore of a missing/mismatched declared native file,
use `archive_policy_json=None` and pin its exact current hash, or
`before_sha256=None` for an explicitly missing file. This mode restores only the
sealed original bytes; it does not adopt current payloads or guess missing
money/history. The coordinator itself and unknown/unsealed roots are not repair
targets. Current account differences, unknown components, policy/segment
conflicts and symlinks fail closed.

Call `store.repair(command, approval=..., facts=..., cut=...)`. First signed
authorization bytes are retained under `state_dir/source_repairs`; conflicting
operation-ID reuse is denied. Exact fsync/CAS/freshness checks precede native
replacement. Interrupted replacement supports exact cold readback/fresh retry,
without another financial operation. Cancelled awaiters do not free the worker
or account lock early. Close maintenance before normal source readback or
target import. Normalization/repair never extends a session, reprices history,
resets a budget, releases a hold or grants activation.

### Retention and qualification

This is host-local file lineage. Retain every retired source coordinator,
signed ancestry/artifacts and the shared recorder database. Missing/tampered
origins deny startup; do not delete an old state directory after import.
Ancestry depth is bounded to 16 generations, with explicit configured
byte/row/operation capacities. Portable restore/import remains PG6 and external
credential fencing remains PG4/PG9. Archive normalization requires a prior
sealed source; arbitrary legacy adoption stays unsupported.

`storage/test_target_import.py` and `storage/test_archive_maintenance.py` add
47 cases. The final C1–C4 plus native accounting/budget/session/WAL/product/
recorder/executor/runner selector passes **2,343 tests**, zero failures/errors/
skips, 22 existing-category warnings in 377.64 seconds. Changed-file hooks and
diff checks pass. Logs/XML stay in `/tmp`; no evidence file is added. See the
[final plan qualification](../../../../docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md#pg1-c3c4-file-closure-and-qualification-2026-10-16)
for the complete selector scope and remaining phase gates.


## PostgreSQL received ACK ingress

`postgres/ack_ingress.py` captures an accepted HTTP ACK before its financial
application. This is an untrusted diagnostic append on the bounded safety pool,
not a financial transaction or a grant. A narrowly allowlisted transaction route
permits only raw capture and delivery marking to omit financial/owner-clock
guards. It cannot be used for arm/allocation/accounting SQL. Original financial
ACK application still checks its own binding, owner, cut and money invariants.

The transport pins its original runtime/repository/owner, captures the primary
receipt time, then applies accounting and records delivery. Pending ingress
blocks new risk until the immutable financial ACK and original wire hash match.
A local receive epoch revokes earlier captured final-send permission before the
first storage await. Cold reads/replay retain original identities, funds and
one-use transport state; they never repeat an exchange submission. Explicit row
and byte bounds are required for pending reads. Unqualified/corrupt financial
state retains received facts rather than silently discarding them.

This increment covers accepted ACKs only. Authenticated FILL/BILL capture,
provider reconciliation, approved pending resolution and configured full-product
service/source composition remain required by PG3. No SDK dedup restoration or
configured PostgreSQL activation follows from an ingress receipt. A crash before
raw capture commits still requires fresh exchange reconciliation; the original
HTTP response in a test fixture is not a live recovery source.

## PostgreSQL provider observation context

`PgSpotObservationReader.observation_context(binding)` implements the shared
read contract over one primary statement. Its constructor requires explicit
row/byte budgets; SQL checks bounded identity/size metadata before aggregating
payloads and checks final encoded size. Returned policies, original preparations,
permanent fill/fee values, applied cashflows, pending received ACKs and the cut
share a snapshot. Concurrent money commits cannot produce a mixed context.
Money uses decimal strings and parsing is independent of ambient Decimal
precision. Runtime connections pin UTC and reject timezone drift before use.

Missing imported policies, malformed/corrupt/foreign origins, or unresolved
financial fills/cashflows deny this read. They never become empty "complete"
lists or newly reconstructed valuation prices. Retired preparations remain
mandatory origins of retained attempts and prepare receipts; absence also
invalidates the current financial snapshot and denies preparation. This is
relational consistency under the declared trusted database-owner contract.

The reader does not authenticate a provider, collect HTTP/WebSocket responses,
qualify runner scope or grant a send. Authenticated collection, financial pending
resolution and full configured service/product composition remain PG3 work.

## PostgreSQL original financial command intake

`PgFinancialIngress` durably captures typed original FILL/CASHFLOW commands
before monetary application. The journal is untrusted diagnostics; it does not
prove a provider response, authorize an order or install a configured service.
Capture survives unrelated financial inconsistency and preserves owner/binding,
exact units, signed fees and chosen independent valuation. Pending intake denies
new risk, including generic allocation. Record hashes cover body, primary capture
time (timezone-independent epoch) and monotone capture sequence.

`capture_and_apply` requires explicit read budgets and serializes the handoff on
the original account runtime. Earlier pending values cannot be replaced by a new
reference choice. Capture, financial application and delivery each have physical
COMMIT/readback boundaries. Failure/cancellation retains uncertainty, original
identities, holds and local revocation. A financial `RECOVERY_REQUIRED` result
stays pending and is never labeled delivered. Successful readback never restores
send permission or resets spending.

Delivery requires the original binding/owner and actual APPLIED financial facts.
A newer complete fill receipt may cover an older received snapshot only when all
original trade/fee/valuation facts match exactly; its cumulative amount/loss must
match the native permanent facts. The historical covering operation is retained
for replay. Thus replay can return the covering financial receipt's larger
cumulative quantity; callers must not treat that quantity as a new wallet delta.
Cashflow identity/units/reference values likewise remain immutable. No balance,
fee buffer, subsidy or terminal hold is refunded by delivery.

Monotone progress counts both ACK and financial capture/delivery. Missing pending
rows cannot silently become an empty recovered account, including a lost ACK
capture after financial ACK application. Current context includes typed pending
commands and rejects a different original binding. Read-only access remains
account-scoped and bounded; private progress writes are never granted to runtime.
This detects relational/hash inconsistency under the declared trusted database
owner contract, not coordinated privileged rewriting.

Authenticated raw provider collection, approved pending resolution, remaining
risk/settlement/shared-product ports and complete controller/service/SDK cache
composition remain required by PG3. This typed handoff is not that source factory.

### Explicit PostgreSQL authenticated SPOT financial observer

`PgSpotAccountReader` and `PgSpotFinancialObserver` can be assembled with an
existing `PgQuoteAccountSource` and original imported owner in offline tests.
The reader exposes fixed private GET routes only, signs/verifies the complete
query, forces UID collection at both edges and requires explicit read, decoded
byte and time limits. Response byte limits apply after JSON decoding; this is
not a streaming HTTP size cap. Credentials/headers are never stored in SQL.

The observer checks account-wide/per-order fills, original order terms, an
anchored complete bill scan, signed fees, imported cashflow approvals, regular
and algo orders, liabilities and the causal cash wallet projection. The shared
pure parser retains file-backend semantics. Decimal wire fields must remain
strings. Independent historical LIFE values are supplied explicitly; pending
untrusted choices must equal that original value and cannot silently reprice.

All chosen commands persist through `PgFinancialIngress` before any monetary
application. Causal application and original receipt/delivery repair do not
resend, release holds, finish orders, activate a cold journal or restore SDK
cache state. In-flight collection/accounting fences new risk locally; safety
reads remain available and failures never restore readiness. Explicit bounds
or any incomplete/conflicting source deny the cycle.

This collector is not installed by configured controller startup. Complete
runner/source/service/shared-product qualification, native financially pending
resolution, durable raw-response diagnostics, SDK listeners and approved live
market provenance remain separate PG3 work. `.env`/configured DB is unnecessary
for the disposable test runner.

### PostgreSQL financial qualification before SDK dedup recovery

`PgSpotDedupRecovery(reader, events, local_idle=...)` and the idle-only
`PgRecorderApplication.restore_spot_dedup(reader)` restore only financially
qualified delivered reporting IDs on the original signed-source connector.
Native ACK/fill scope, exact amounts/prices and signed fee asset/value must match
APPLIED financial facts at the same primary cut. Report timestamps remain SDK
observation times. Financial-only facts without reports are not added to cache.

Both UID edges, the final primary cut and the original runtime/binding/owner and
local idle state are checked. Pending/unknown facts and unknown existing cache
entries deny; they are not silently cleared. Actual synchronous SDK publication
failure restores the original cache and revokes local health. No tracking JSON,
order tracker, hold release, owner, trading readiness or sender is restored.
The configured service/listener factory remains unimplemented and denied.

### PostgreSQL standalone inventory settlement

`PgInventorySettlementRepository` implements explicit settle/resume/lookup for
closed SPOT inventory cycles. Original financial facts determine actual spend;
there is no caller release amount. Every member needs a qualified terminal
receipt and the cycle must net to zero LIFE including fee asset effects.
Actual subsidy is at least gross execution loss plus any uncovered realized
cycle cost. The same atomic transaction releases original committed subsidy
holds and records actual incurred spend in original UTC budget buckets.
Wallets and execution-loss counters are never credited, and other orders keep
their holds. Cycle/member identities cannot be reused.

A missing/corrupt cycle, member, receipt or spend invalidates current state.
Primary cut, current original owner and explicit imported fact-age policy are
checked at physical COMMIT. Cold recovery reads the original operation before
retry; a fresh observation after definite absence retains every economic field.
Historical receipts never activate a sender. This standalone port does not
install the configured service or qualify joint/product settlement. Migration 000024 qualifies with 903 full PostgreSQL cases and 31 migration
cases; the full PG3/controller/shared-product gate remains open.

### PostgreSQL standalone SPOT markout

`PgSpotMarkoutRepository.observe/lookup/frame/summary/evaluate` preserve exact
independent horizon values over original native accounted fills. SQL requires
an explicitly imported policy, exact wire types/fingerprint and current original
owner/cut; physical COMMIT rechecks generation and fact age. Bounded coherent
frames preserve pending/missing and insufficient/adverse cohort distinctions.
Original prices cannot be replaced through replay or a new operation identity.
Aged observations remain permanently readable, while expired window samples
cannot make an empty cohort ready. Diagnostic receipts/decisions always carry
`trading_permission=false`; no wallet, fee, subsidy or execution-loss value changes.

Use the disposable runner with `--suite markout`. The 50 focused cases include
real process exits/backend termination, original cold readback, exact signed
BUY/SELL values, shared tracker parity and corruption/access/bound checks.
Configured risk/stability/probe authority, authenticated horizon sourcing, shared
products and complete PostgreSQL service composition remain separate PG3 work.

### Signed recovery reads without quote eligibility

`PgObservationAccountSource.create(journal, ...)` supplies the same pinned HMAC,
endpoint/environment and forced UID collection checks to `PgSpotAccountReader`
without requiring a financially verified quote policy. Factory and collection
edges check current primary owner/binding; late reads from the expired current
original owner remain observations. The separate exact type is refused by the
quote guard. Guard/capture/age-limit APIs deny and its final send check is false.
Native financial pending/HALT state, wallet/holds and operator approval are not
resolved or renewed. Financially qualified idle SDK dedup remains a separate
explicit application; configured service and approved pending repair remain open.

Runtime admission reads primary/durability/TLS, bounded migration history,
namespace/schema and actual role privileges in one uncached primary statement.
Catalog names/operators/types are pinned and session `search_path=pg_catalog`
is enforced; TEMP catalog lookalikes cannot hide real DML or BYPASSRLS grants.
This read-count reduction is not a whole-history or latency qualification.

### PostgreSQL provider response diagnostics

`PgSpotAccountReader` requires `PgProviderJournal` COMMIT before returning an
admitted decoded GET response to its consumer. It retains original parseable
provider errors and replies followed by a local context rejection. Capture or
unknown-COMMIT failure returns no parser data; HTTP is never implicitly retried.
Only fixed private GET paths/query keys are accepted; headers/credentials and
arbitrary URLs are excluded. Non-JSON/over-budget responses remain denied.

Migration `000027` keeps immutable pages and monotone collection heads, bounded
to 512 pages/16 MiB including metadata. `collections(after=..., limit=...)`
provides indexed bounded cold discovery; `read_collection` checks sequence,
count, bytes and hash links/tip with explicit row/byte limits. Discovery is not
a stable account snapshot; concurrent appends can sort before a prior cursor.
The declared trusted database-owner contract still applies to colocated hashes.

These are decoded JSON diagnostics, not raw HTTP/UID response bytes or financial
facts. Append/read changes no financial cut, balance, hold, pending fact, budget,
SDK cache, completeness or trading permission. The authenticated collector must
still qualify the whole causal account before original typed intake/accounting.
Configured startup/source/service, approved pending repair and all enabled
shared-product ports remain required for full PG3 closure.

### Explicit PostgreSQL SPOT connector ingress

`await reporting.install_spot_events(observer)` installs actual OKX listeners
on the same pinned connector used by `PgSpotFinancialObserver`. Startup performs
signed whole-account collection and native financially qualified SDK dedup;
listeners attach before awaited qualification to retain startup callback hints.
The assembly requires the original registered SPOT reporting source, account,
owner, controller body, repository/runtime and credential identity. A known
legacy `MarketsRecorder` listener or another event application is rejected.

Callbacks are untrusted hints/reporting, never financial fills or SDK cache
authority. They invalidate old local captures synchronously and enter a bounded
queue. The pump drains native reporting, collects signed account facts and then
qualifies SDK dedup against native financial/report receipts. Bursts coalesce;
changed context, inconsistent facts or capacity exhaustion latch denial. No
tracking JSON/order tracker or readiness/send permission is restored.

`await reporting.close()` first detaches these listeners and awaits owned
observation. Timeout/caller cancellation cannot cancel physical financial work
or release its local capacity early; `PG_EVENT_SHUTDOWN_READBACK_REQUIRED`
requires readback. Stop never publishes SDK IDs late or refunds a hold. This
explicit offline assembly leaves configured LIFE controller/service selection,
qualified planner/market provenance, financial pending repair and shared-product
ports open. It does not qualify a live account or external sender exclusivity.


### PostgreSQL native shared SPOT/SWAP capital allocation

`PgSharedCapitalRepository(financial, owner=..., observation=..., max_rows=...,
max_bytes=...)` implements the shared-capital product port over native account
transactions. Install an explicitly approved immutable model/capacity first.
The observation callback must qualify the whole account; its DTO/source strings
are not authentication. `reserve(ReserveCapital(...), cut, owner)` and
`check_capital(binding)` require the current active owner. `claims`, `frame` and
original operation `lookup` are bounded diagnostic reads; receipts implement
`ProductReceipt` with account atomicity and `trading_permission=false`.

Migration `000028` stores original observations/operations and typed claims,
including permanent wire/session/config/risk identities. SQL calculates all
independent fill endpoints, no prospective hedge proceeds, fees, funding,
basis/directional stress and account/tiered IM/MM. Exact rational margins round
up to 10^-18 USDT only after combining original account and stress requirements.
File uses the same margin rule and fixed precision for the native bounded domain.
SELL inventory and hard delta endpoints also include possible LIFE fees at
the conservative modeled rate. Native SPOT prepare/arm requires its original
matching shared claim, current config/risk epoch and enough modeled fee allowance. Abort can release native
order holds but cannot release the shared claim. Exact receipt retry retains
original observations and never calls the source again or changes a wallet.

Explicit imported capacity and read row/byte limits remain mandatory. Native
corruption/role/source/identity denials, concurrency, actual process/backend
COMMIT loss and unchanged 1,000 ms physical expiry have offline coverage. This
allocator does not install SWAP sending/accounting, funding credit, joint release
or APPLYING recovery, qualified live source, configured LIFE service, ownership
handoff or production eligibility. Existing configured PostgreSQL rejection
remains until the full PG3 composition is qualified.

## Native PostgreSQL carry evidence (PG3 increment, 2026-10-17)

`postgres/carry.py` implements the carry port through migration `000029` fixed
account transactions. It requires the original `PgSharedCapitalRepository` and
an explicitly qualified `CarryBundle` callback. This callback is not provider
authentication. Independent monotone streams, immutable funding bills, dynamic
cadence/horizon, worst gross funding costs and dynamic fee/tier evaluation use
native SQL. Funding evidence never credits a wallet: settled funding is already
included in the observed collateral; future receipts do not finance risk.

`observe` binds one explicit operation to its original cut/owner. `lookup` and
exact replay return the original receipt without calling the callback or renewing
its timestamps. Every receipt has false trading permission. `check_carry` creates
a new evidence operation. Invalid/missing feeds persist a denied observation and
primary clock advance while preserving prior qualified streams. Expensive newer
streams remain retained on economic refusal. Frame/readback require explicit row
and byte limits; corruption and incomplete permanent payment history deny.

When a carry policy is imported, SPOT prepare/arm additionally requires current
carry readiness; a new shared observation/claim revokes the older result. Late
ACK/accounting remains observation-only after feed denial. Actual process exit,
lost backend reply, current-generation and physical fact-age checks cover the
storage boundary. The migration preserves the old snapshot OID and ACL. The
configured controller/service, SWAP sender/funding cash accounting, joint
settlement/recovery and approved pending resolution remain unported. Retained
check replay is not qualified scalable history; PG8/O.8 remain open.

```sh
make life-postgres-test LIFE_PG_TEST_ARGS="--suite carry --output /tmp/life-carry-run"
```

## Native PostgreSQL SWAP intents (PG3 increment, 2026-10-17)

`postgres/swap.py` implements the preparation/arm/ACK storage slice using migration
`000030`. It requires the original shared-capital repository, an imported exact
contract/capture policy, a retained matching SWAP claim and current carry facts.
It stores the complete original `PrepareSwap` config, derives the same permit
units as the File sender and commits intent plus receipt atomically. Contract
counts never replace LIFE quantities. Account budgets, capture age and current
session/config/risk scope gate new risk at entry and physical COMMIT.

`arm` irreversibly records SEND_UNKNOWN; repeated arm denies. Exact late ACK may
record an original exchange ID after HALT/expiry, without credit or claim release.
`lookup` is bounded diagnostic readback with false trading permission. Common
SPOT/SWAP cuts retain all SPOT money/ingress guards and verify the original SWAP
config, payload, unit conversions and event progression. Actual COMMIT process
and backend loss recover original metadata without an exchange request.

The initial storage checkpoint denied `authorize_boundary`. Migration 42 now
provides an exact-byte diagnostic arm through that method; the separate signed
request guard owns the one-use HTTP boundary described below. Configured
controller installation and the remaining PG3 closure workstreams stay open.

```sh
make life-postgres-test LIFE_PG_TEST_ARGS="--suite swap --output /tmp/life-swap-run"
```

## Account-source failure revokes local admission

A new shared-capital refresh that is cancelled, fails serialization/type checks or
is rejected for account/wallet/sequence/native-order context revokes its original
`PgRuntime`. Carry refresh cancellation also revokes it. The previous stored
ready decision remains diagnostic; it cannot admit another new-risk command on
that runtime. Ordinary carry feed failure records its existing durable denial.

A revoked runtime may use the safety lane for a diagnostic capital OBSERVE. The
SQL ACTIVE-owner/session/expiry checks remain mandatory, successful observation
never heals local readiness, and RESERVE stays on the denied new-risk lane.
Financial state and original claims are retained. Rebuilding a qualified runtime
is separate from receipt/readback and remains subject to configured source and
PG4 ownership gates. No new schema, financial default or activation is added.

The File carry evaluator uses an isolated 512-digit Decimal context, consistent
with shared capital. Long legal-unit funding/price/exposure products match the
native SQL decision under caller precision 6 or 28. A fast independent rational
forecast test covers the same monetary guarantee without PostgreSQL. No policy,
permanent funding bill, wallet amount or caller precision is rewritten.

The private migration-31 SWAP position kernel matches the existing File joint
replay's explicit decimal28 half-even arithmetic, including weighted basis and
reported close PnL. It validates identity, units, chronology and bounded work,
and returns cash events without writing money. It has no runtime EXECUTE grant.
File joint replay now owns/restores that arithmetic context so caller precision
cannot redefine retained facts. Causal native joint wallet application, funding
settlement and claim release still require their domain command and recovery
proofs; this arithmetic kernel is not a configured financial backend.

## Native derivative fill accounting

`PgSwapFillRepository` accepts complete original `FillSwap` snapshots. An explicit
imported opening/coverage/work policy is mandatory. SQL recomputes basis/PnL,
keeps permanent signed fill/fee identities, applies only new actual cash effects
and charges gross loss in the original budget windows. Failures retain pending
facts/HALT after atomic rollback of provisional money. Exact readback around
unknown COMMIT never reapplies a fee or releases a shared claim. Fill-before-ACK
never advances an attempt; a late ACK must match observed fills. Read/reporting
results grant no trading permission. Stale account positions cannot be reused
for new risk, including when an open has zero fee. Observation bounds and source
failure revoke local admission; diagnostic refresh does not heal it.

The standalone SPOT NAV port is disabled for a SWAP-accounting account because it
omits derivative unrealized PnL. Native joint portfolio NAV,
terminal/release and recovery remain unported. Provider-authenticated source,
controller/runner/recorder assembly and configured PostgreSQL selection remain
required. This standalone financial slice installs no external SWAP sender.

## Native settled funding cash

`PgFundingRepository` consumes a complete caller-qualified `CarryBundle` in an
original `ApplyFunding` command. Explicit opening-cut, history, record and byte
budgets are required. Only new settled bills change USDT. Paid bills before the
opening cut are already in opening cash; future/expected payments never credit
money. Gross funding cost stays separate from transfers and execution losses,
never nets against later receipts and cannot reset its budget. Failed financial
application retains source/pending/HALT without partial money. Old account/carry
must be refreshed to cover permanent bills, including zero payments. Readback
and receipts authorize no send/release/activation. Changed receipt context after
COMMIT requires original primary readback and cannot relabel money.

Coupled joint application, portfolio NAV, terminal/shared claim release, pending
resolution and configured authenticated source/controller assembly remain open.
Disposable functional tests use an explicit 6 s outer I/O budget with the existing
1 s statement limit; deadline fault tests set their own smaller budgets. Those
synthetic budgets do not qualify live latency or PG8/O.8 performance.

## Native PostgreSQL portfolio NAV (PG3 increment, 2026-10-17)

`PgPortfolioRepository` accepts only `MeasurePortfolio(operation_id)`: callers
cannot supply a NAV amount, high-water or permission. Migration-owner import
explicitly provides opening mark, drawdown/source-age and record/byte limits.
Every immutable observation binds the native carry/account/mark/index cut to
cash, approved flow valuations and leg basis replayed at its original version.
Signed funding/fees/realized PnL affect settled cash once; unrealized leg PnL
belongs to NAV; reported collateral is never added as another wallet.
Historical measurements remain verifiable after later economics. A new current
measurement requires fresh matching source coverage, including at COMMIT.
Drawdown exhaustion records truth and latches HALT without refunding claims.
`current` describes a fresh valuation only: receipts always grant no trading.

Qualification: 20 native observation/recovery cases, 17 arithmetic cases and 41
migration cases pass; see the existing implementation plan for exact source and
logs. Actual File joint financial projection parity is valuation coverage, not
coupled release/settlement parity. Native portfolio batch application, provider
collateral reconciliation, terminal/shared release, joint recovery, hedge and
configured authenticated services remain required. PostgreSQL controller
selection and SWAP final transport stay explicitly refused.

## Native PostgreSQL SWAP terminal observations (PG3 increment, 2026-10-17)

`PgSwapTerminalRepository` binds an explicit original ACK/order to its complete
native cumulative fills and original versioned portfolio/account source cut.
Migration-owner import explicitly supplies terminal record/byte bounds. Current
source freshness and original ownership are checked again at physical COMMIT.
Filled/canceled terminal receipts are permanent observations, not release grants.
All shared claims remain held. A wrong order, missing fill, incomplete coverage,
still-pending order or cancel-ACK source retains the original observation,
ACKED state, unchanged money and HALT. A new financial observation after terminal
is permanent pending/HALT; original exact retries retain their old receipts.
Nothing automatically clears that pending fact or reactivates risk.

22 native terminal cases have passing coverage (21-case suite plus actual-expiry
case); 42 migration and 300 affected-domain cases pass, including process/backend
COMMIT loss, original fresh-object readback, physical source/owner expiry and
corruption/role/work bounds. Source labels do not authenticate provider facts.
Native coupled batch accounting, collateral reconciliation, shared release,
joint recovery, signed pending repair and complete configured source/service
assembly remain required. Configured PostgreSQL/SWAP transport stay refused.

## Native original shared-claim retirement (PG3 increment, 2026-10-17)

`PgSharedReleaseRepository` accepts `ReleaseSharedClaim` only for an original
qualified SPOT/SWAP terminal, current version-bound complete portfolio/account
source and zero unresolved financial observations. Physical COMMIT rechecks
source freshness and original owner. The claim moves from active allocation to
permanent settled history; balances, actual exposure and gross spending do not
change. Permanent identity capacity is not reclaimed. The original ID cannot
prepare or allocate again; historical decisions and receipts retain their
original version. Current carry must be explicitly refreshed before new risk.
Cold/concurrent retries never refund money, reactivate trading or release twice.
20 native, 43 migration and 10 DTO cases pass; exact qualification is in the
existing implementation plan. Coupled financial batches, collateral/pending
reconciliation, hedge and configured service/controller assembly remain open.

## Native coupled financial batches (PG3 increment, 2026-10-17)

`PgJointBatchRepository.apply(ApplyJointBatch(...), cut=..., owner=...)` accepts
complete original SPOT/SWAP fill commands, approved cashflow commands and one
complete paid-funding source. Original final account facts are never rewritten
into imaginary intermediate accounts. Private transaction-scoped kernels stage
facts and gross costs; complete original-time replay then validates the one
final wallet/leg cut. An earlier actual payment can fund a later debit; later
income cannot finance an earlier deficit. Already applied exact members retain
their original receipts. A failed member or final replay rolls back all staged
money/costs and retains the original entire batch with HALT. New IDs cannot
bypass that pending state. The signed source-repair profile below is required
to resolve a retained parent; ordinary publication never clears it.

Runtime/reporter roles cannot execute the stage kernels or read/write their
capability table. A stage capability cannot survive COMMIT. Explicit bounds
count retained parent envelopes, original member requests/receipts and both
money cuts; overflow rejects the whole account transaction. Ownership/source
freshness are checked again at physical COMMIT. Original cold readback never
resends, refunds or grants trading. Independent source/carry refresh, NAV,
terminal and claim retirement compose in an explicit cold replay, retaining
position exposure and all spent gross budgets. 28 native, 44 migration and 11
DTO cases pass; see the existing implementation plan for exact scopes/logs.

This is not a legacy `APPLYING` importer, signed pending resolution, actual
collateral/liability reconciliation, native hedge/risk/control or configured
controller/runner/recorder/source/final-SWAP-send assembly. Configured PostgreSQL
selection remains denied until those required native routes are installed.

Product repositories pin their original financial/runtime/binding/owner and full
runtime configuration. Capital/carry/SWAP parse a committed result only against
that original context. A change after COMMIT returns `CommitUnknown`, revokes
local admission and needs original primary readback through fresh correctly
bound objects. No result can relabel a committed allocation/intent or restore
permission. Fifteen actual PostgreSQL substitution/readback cases pass.

## Late capital observations (PG3 increment, 2026-10-17)

Only original current-owner OBSERVE uses the safety queue after HALT,
DRAINING/RECOVERY_ONLY or owner/session expiry. Complete fresh source, native
money/leg, original generation and physical COMMIT proofs remain mandatory.
This does not allocate, reset deadlines/spending/HALT or heal local admission.
An allowed capital decision is an economic observation, not permission. The
budget-HALT → account/carry → NAV → terminal → original-claim retirement chain
passes while funding cost stays exhausted, actual exposure remains and a fresh
runtime is denied new allocation. Eleven native and 45 migration cases pass;
signed pending repair, legacy import, collateral/source qualification, hedge and
configured services remain PG3 work.


## Signed joint source repair (PG3 increment, 2026-10-17)

`PgJointRepairApprovalImporter` uses a separate schema-owner connection and external
public Ed25519 keys pinned to the registered configuration. Its existing RECOVER
role authorizes the exact `ResolveJointSources`, original primary cut and complete
`JointRepairFacts`. Policies specify positive approval/repair/byte/age budgets;
there are no live defaults. The owner receives no scoped runtime access mapping.
Importing a grant changes no financial cut or money.

`PgJointRepairRepository.resolve` uses one native account transaction. Replacement
sources may refresh the final account/funding bundle, retaining original fill/fee
and cashflow commands and every existing funding bill. Original native validation
and causal wallet/leg replay remain mandatory. A rejected replacement rolls back
all new children. An accepted replacement appends a resolution link, preserving
raw pending history, HALT, held claims and gross spending. An independent pending
parent stays unresolved; an equivalent duplicate needs another signed command.
Captured unused member IDs stay retired. Direct native pending families are not
bypassed. Runtime/reporter roles cannot import approvals or access the private
transaction capability; expiry and ownership are rechecked at physical COMMIT.

This profile cannot rewrite wrong monetary observations or clear direct native
SPOT/SWAP/terminal pending facts. Migrations 44–45 extend it to the direct
funding profile below. It authenticates operator approval, not provider facts.
Legacy import, full control/hedge and configured joint collection/
runner/recorder/final-send assembly remain required. PostgreSQL selection stays
denied. Run isolated qualification with `--suite joint_repair` in the central
`storage.run_postgres` runner; use disposable loopback PostgreSQL only.

### Direct funding pending repair (migrations 44–45)

The same `ResolveJointSources` may target an original standalone `PAID_FUNDING`
operation in `RECOVERY_REQUIRED`. The replacement has no SPOT/SWAP fills or
external cashflows; it refreshes complete account/funding/mark/index/terms facts
and retains every original bill ID, signed amount and settlement time. It uses
new replacement operation IDs. No synthetic pending batch is inserted. Native
causal cash/position replay, collateral and liability gates still apply.

The private capability binds the exact repair ID, pending ID and replacement in
the current transaction. Another approval with identical replacement JSON grants
no additional bypass. Migration 45 permits an explicitly signed funding cohort:
set `ResolveJointSources.additional_pending_operation_ids` to an immutable tuple
of additional original direct-funding pending IDs. The tuple is canonicalized;
the field is absent from the original single-parent signed JSON/receipt. A
cohort must preserve every selected original bill, including overlapping bills
and distinct debit/credit histories. It cannot include SPOT/SWAP/terminal or
already consumed origins. Another direct pending observation outside the chosen
group blocks publication. The original pending row/receipt
stays immutable and visible in `funding.frame()['pending']` for diagnostics.
Current unresolved flags clear only through the committed resolution link;
historical terminal/release proofs use its original committed version. Repair
does not clear HALT, release a claim or authorize trading. Fresh account/carry/NAV
and independent terminal/release commands remain required afterward.

The existing imported `joint_batch_policies.max_members` additionally bounds the
number of original pending operations per cohort. Existing repair byte capacity
counts approvals, root links and additional immutable membership rows together.
No capacity or economic threshold is created by the migration. Members are
consumed permanently across both primary and additional target positions.
Actual economic ordering still determines whether a debit can be financed;
later income never funds an earlier deficit or nets away gross funding spending.

Use `--suite funding_repair` / `--suite funding_cohort` for these profiles. The `joint_recovery` selector also
includes ordinary funding and these tests. All runners use disposable loopback
PostgreSQL with synthetic sources, never the configured `.env` database.

## Native PostgreSQL hedge coordination (PG3 increment, 2026-10-17)

`postgres.hedge.PgHedgeRepository` pins the original native SWAP/shared-capital
runtime, owner and binding. `UpdateHedgeTarget`, `PlanHedge` and `SettleHedge` carry
permanent operation IDs and original version cuts. Target changes cannot clear an
in-flight child or a cost fault. No File journal is instantiated by this adapter.

SQL sizes against actual SPOT plus long minus short inventory, closes an opposite
leg first, floors exact contract lots and checks executable depth, basis, fee,
funding, edge, exposure age, child size, retry and cumulative cost limits. A child
publishes shared claim → carry check → captured native SWAP preparation in one
account transaction. Its fully verified immutable prefix is reused only inside
the private locked three-command context; full final/physical-COMMIT checks
remain and no proof cache survives COMMIT. A rejected child rolls those writes back while retaining its
attempt and conservative cost hold; its captured IDs cannot be recycled. A
prepared or partially filled hedge blocks another child until native terminal
settlement. Retained claims still need the separate qualified release operation.

Settlements derive complete original trade IDs and actual fee/adverse cost from
native accounting. Repeated observations do not debit money again or reduce the
original cost hold. An actual cost breach latches HALT. Observation and cold
readback never arm a child or restore trading permission. Physical COMMIT checks
original ownership and the independent source/market deadlines. Installed policy
also blocks generic SWAP claim bypass and unbalanced or unowned-pending SPOT risk.
A failed market capture cannot implicitly reuse an older good capture. Financial
child plans use active admission, so a locally revoked runtime can only observe.

These are explicit offline repositories and synthetic-source qualification;
configured source/controller/runner installation, pending/risk/control services
and configured final SWAP send composition remain open PG3 work. `storage_backend: postgres`
continues to be rejected by the configured LIFE controller.


## Explicit native SWAP HTTP and received ACK composition

`postgres.swap_send.PgSwapBoundary` atomically binds the exact original HTTP
body to native SWAP arm under migration `000042`. Contract counts, base LIFE,
position side and close/reduce-only semantics must match the captured permit.
Owner, session, capture, carry, funding and hedge deadlines constrain the
original write window at physical COMMIT; retained readback cannot rearm.

`postgres.swap_transport.PgSwapAccountSource` binds the actual derivative
connector's signed UID GET and order bytes to pinned credentials, endpoint,
environment and explicit UID/auth/clock/queue limits. Its required local fence
is an assembly input, not proof of complete SDK runner scope. A final one-use
synchronous check follows the last await and conservatively subtracts allowed
clock skew from the DB deadline. Retry, redirect, changed source, local stop or
unknown COMMIT never restore that capability. Secrets/auth headers stay local.

`postgres.swap_ack.PgSwapAckIngress` captures an accepted strict single-order
ACK before native financial application, then records exact delivery. Cold
pending readback repairs that original response once; it never sends, refunds
claims or clears HALT/local revocation. A response lost before capture commits
still needs authenticated provider reconciliation. Generic SQL arm/readback
receipts remain diagnostic and cannot become HTTP guards.

This is an explicit assembly exercised with disposable PostgreSQL and fake HTTP.
Configured PostgreSQL service/controller, qualified shared-product collectors,
runner/control/risk and the remaining PG3 acceptance contracts remain open.

## Native joint collateral bound

Migration 43 composes the collateral bound with joint batch publication and
historical proof, portfolio basis, terminal/shared release, and (when the
portfolio policy is installed) current shared/carry admission. For the dedicated
account-mode-2, cross-margin USDT scope, the ceiling is
`max(0, settled USDT cash + replayed SWAP mark-price UPL)`. LIFE inventory NAV is
excluded. Rounding may lower the ceiling but cannot raise it above exact native
cash/UPL equity. Collateral may be lower due to a conservative haircut. This check does
not credit cash, deduct funding twice, release claims or activate trading. The
Python portfolio adapter permanently revokes its local runtime after an invalid
collateral source; cold current-source gates still deny the same invalid input.

The source must represent the same complete account cash/position scope as the
native replay. A strategy allocation, available balance, `totalEq` in USD or an
unqualified aggregate `adjEq` is not an interchangeable USDT cash observation.
See the [OKX balance field definitions](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-balance).
Provider authentication, complete currency/liability reconciliation and a
qualified source mapping are still required in PG3. The existing File fixtures
include independently supplied collateral risk observations; arithmetic
comparison to File decimal28 NAV is not qualification of every File collateral
model. Neither storage-only profiles nor these tests enable configured PostgreSQL.

Focused existing-workflow regression (disposable local PostgreSQL only):

```sh
python -m test.hummingbot.strategy_v2.life_liquidity.storage.run_postgres \
  --suite joint_recovery --fail-fast --output /tmp/life-pg3-joint-recovery
```

It includes batch, signed joint/direct-funding source repair, ordinary funding,
NAV, terminal, shared release, late
observations and collateral tests. It does not replace whole-service acceptance
or the other four fixed PG3 workstreams.
