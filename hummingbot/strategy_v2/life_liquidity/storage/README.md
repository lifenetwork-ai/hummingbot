# LIFE storage contracts

`contracts.py` defines immutable `StateBinding`, `StateCut`, resource versions,
owner facts, snapshots and commit receipts, plus `StateRepository`,
`OwnershipRepository` and `UnitOfWork` protocols. These describe storage reads
and publication; no returned value authorizes an exchange request or releases a
financial reservation.

## Current implemented scope

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

## Read/write contract

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

Current service scope remains an explicit offline library assembly. Simultaneous other partial
families and unadmitted external transfers, broader signed policy/lineage/archive
transitions, approved nested financial configuration changes and production source
factory qualification remain open. Actual runner/REST tests use explicit
synthetic listing/feed releases. PG1 stays open for those routes and complete
coupled parity; O.8, PostgreSQL, demo, live and deployment gates are unchanged.
