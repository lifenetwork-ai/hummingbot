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

## Tests

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
cut to capture arbitrary observations. Terminal attempts, recovery completion
and transport authentication remain future command/bridge work. ACK after an
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

### Spot tests

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_financial_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_ack_contracts.py
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_fill_contracts.py
```

Tests exercise the actual journals, BUY/SELL and fee claims, budget limits/spending
readback, slot/wire identity, concurrent/stale commands, expiry/owner/binding,
cancelled preparation and account exclusion, cold rearm denial and kill-process
interruptions after principal reservation and durable unknown-send publication.
All balances, UIDs, policies, clock values and capacity (2 normal submissions,
1 MiB journal/coordinator capacity, 128 coordinator stage IDs plus one cleanup
slot) are explicit synthetic fixtures. They provide no actual exchange proof.
