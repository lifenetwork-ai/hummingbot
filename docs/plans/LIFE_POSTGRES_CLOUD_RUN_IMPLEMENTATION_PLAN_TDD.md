# LIFE: PostgreSQL and Cloud Run Implementation Plan (TDD)

Created: 2026-10-11

Status (2026-10-12): **Disposable local spot feasibility/assembly measured; central migration infrastructure and offline checkpoint/file adapters implemented; spot preparation, ACK, fill/fee loss/subsidy, approved cashflow/NAV and exact terminal/resume storage slices implemented. General recovery, filled economic settlement, controller integration, PostgreSQL financial repositories and deployment remain pending.** Parent delivery checkboxes remain open; checked subitems below describe only their explicit scope. The [spike evidence](LIFE_POSTGRES_SPIKE_EVIDENCE_2026-10-12.md) records real PostgreSQL transactions with synthetic transport, not production migration or exchange proof. The existing [LIFE implementation plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md) remains the source of trading requirements and O/D/R/C release gates.

Source reviewed: HEAD `e8a2fd427` plus the working-tree changes recorded in the [serialized qualification evidence](LIFE_OKX_O8_SINGLE_SCOPE_EVIDENCE_2026-10-11.md). Freeze exact source/config/schema/image hashes when implementation begins; HEAD alone does not identify this working tree. O.8.4/O.8.7 are still open, and production trading remains disabled.

## 1. Recommendation and decision

**Implement PostgreSQL as a portable storage backend first. Qualify it on a single VM/container before deciding to move the execution worker to Cloud Run.** Cloud Run is a conditional deployment target, not a prerequisite for using PostgreSQL.

PostgreSQL is useful for transactional financial updates, indexed active-state access, central auditability, and managed backup/recovery. It can replace repeated file-history work only after the state model and verification contracts are redesigned. Changing a database URL or moving whole JSON documents into a table will not resolve the current performance gate.

For one LIFE account, Docker on a VM with durable storage is operationally simpler. Cloud Run becomes more attractive when the team needs standardized managed deployments, isolated workers for several accounts, centralized operations, or integration with an existing Google Cloud platform. The first Cloud Run release accepts manual downtime during ownership changes; it does not promise automatic trading failover.

| Option | Benefit | Additional work | Recommendation |
| --- | --- | --- | --- |
| VM + current file backend | Closest to the current single-host model | Existing O.8 work, durable disk, backup and operations | Valid baseline while assessing migration |
| VM + PostgreSQL | Transactional authority and indexed state, independent of compute platform | Storage refactor, import, recorder integration, database operations | First PostgreSQL milestone |
| Cloud Run Worker Pool + PostgreSQL | Managed worker revisions and integration with Google Cloud | All PostgreSQL work, lifecycle handling, credential isolation and proven sender handoff | Conditional second milestone |

### Economic decision gate

Compare total monthly cost and operating effort using the same declared account count, CPU/RAM, history volume, availability and recovery objectives:

`compute + database/HA + disk/backups/PITR + network/NAT + logs/metrics + build/registry + engineering/on-call + expected downtime`

Continuous workers consume resources while running; do not assume request-driven scale-to-zero savings for a continuously quoting bot. Use current regional [Cloud Run pricing](https://cloud.google.com/run/pricing) and [Cloud SQL pricing](https://cloud.google.com/sql/pricing), with dated calculator exports. No price or cost saving is asserted here.

**Proceed with PostgreSQL** if measured correctness, latency, audit/recovery, or operating benefits justify the migration. **Proceed with Cloud Run execution** only if its operating benefit also justifies the handoff protocol, ongoing credential rotation and measured recovery downtime. PostgreSQL on a VM remains an accepted final topology if that second decision is negative.

## 2. Existing code and migration scope

| Current seam | Required change |
| --- | --- |
| [Client DB configuration](../../hummingbot/client/config/client_config_map.py), [SQL connection manager](../../hummingbot/model/sql_connection_manager.py) | SQLite is the default recorder; add and qualify a PostgreSQL driver/configuration, schema lifecycle and connection handling |
| [Legacy DB migrator](../../hummingbot/model/db_migration/migrator.py) | Keep its recorder file workflow separate; LIFE PostgreSQL now has a [central migration CLI and SQL directory](../../migrations/life_liquidity/README.md), with full financial/recorder DDL still pending |
| [MarketsRecorder](../../hummingbot/connector/markets_recorder.py), executor orchestrator | Preserve executor, wire-ID, UID and position recovery provenance; qualify PostgreSQL reads/writes and failure handling |
| [LIFE controller](../../controllers/generic/life_liquidity.py) | Replace required JSON paths and path-based install checks with explicit backend/resource bindings; preserve account and policy checks |
| [PolicyState](../../hummingbot/strategy_v2/life_liquidity/policy_state.py), WAL, reservations, sessions, actions, budgets | Introduce typed storage and transaction boundaries; existing file locks/replace/fsync are not SQL transactions |
| [Fill attribution](../../hummingbot/strategy_v2/life_liquidity/fill_attribution.py), spot journal proof, shared capital, joint recovery | Replace file-byte/cache identity with independently qualified database cuts and financial invariants |
| [Account ownership](../../hummingbot/strategy_v2/life_liquidity/account_lock.py) | Keep the file backend's host-local lock; add PostgreSQL ownership and a separate exchange sender-handoff protocol |
| [CLI engine](../../hummingbot/cli/engine.py), [Dockerfile](../../Dockerfile) | Headless foreground worker, structured stdout, bounded signal handling, startup recovery and environment validation |

Migrating the SQL recorder alone leaves LIFE financial authority in files. Scope includes enabled spot, linear SWAP, hedge/carry and inventory paths; an implementation may deliver a declared spot-only slice first, but must explicitly disable and retain open coverage for unsupported paths.

## 3. Target architecture

```mermaid
flowchart TD
    Config[Versioned config and generation identity] --> Worker[One LIFE coordinating worker per risk account]
    Worker --> Store[Async typed PostgreSQL repositories and unit of work]
    Store --> DB[(PostgreSQL / Cloud SQL primary)]
    Worker --> Sender[Protected spot and SWAP sender]
    Sender --> Exchange[OKX REST and private WebSocket]
    Ops[Operator handoff workflow] --> Grant[Generation-bound activation and credential grant]
    Grant --> Worker
    Secrets[Generation-specific secret and service identity] --> Sender
    DB --> Audit[Read-only reporting and cold audit]
    DB --> Backup[Managed backups and tested PITR]
```

The worker may run unchanged on a VM or a Cloud Run Worker Pool. Keep one coordinator for both products sharing the same account capital. Initial scope excludes sharding one account across independently sending workers, automatic leader takeover, active-active trading, portfolio margin, and cross-region trading failover.

Cloud Run Worker Pools support continuous background workloads without a load-balanced HTTP endpoint or automatic scaling. They remain subject to instance replacement and shutdown. [Worker Pools](https://docs.cloud.google.com/run/docs/deploy-worker-pools), [runtime contract](https://docs.cloud.google.com/run/docs/container-contract).

### Storage boundaries

- Introduce backend-neutral `StateBinding`, `StateCut`, repository ports, `UnitOfWork`, ownership and send-attempt services. A binding includes environment, exchange, authenticated account UID, risk pool, database lineage, schema version and policy/config hashes. A cut includes the relevant row/event versions; it is never a reusable send permit.
- Use indexed relational current-state rows plus append-only financial/identity events. Keep complete terminal identities and deduplication evidence. History can be partitioned, but pruning requires its own proven retention/recovery contract.
- Use asynchronous database operations on the trading path. Do not block the event loop with a synchronous PostgreSQL facade. Introduce a bounded LIFE dispatch task at the synchronous V2 tick/filter/dispatch boundary: enqueue an immutable, uniquely identified proposal, await database authorization in the protected executor path, and revalidate expiry/stop/config after waits through the actual post-throttler connector boundary. Do not create an unbounded background task per tick or return a precomputed boolean as asynchronous authorization. Late results after stop cannot issue a new request; already attempted requests remain unknown until reconciled.
- Keep database transactions short. Exchange HTTP/WS requests do not run inside retriable financial transactions. A database transaction cannot atomically commit an OKX request.
- Recorder/reporting data is not a second source of spendable capital. Where recorder persistence is separate, use a durable transactionally recorded projection/outbox operation, idempotent application and an explicit recovery-completeness gate. Missing or stale recorder evidence cannot release a hold.

### Proposed schema families

| Family | Required identity/invariants |
| --- | --- |
| Accounts, ownership, activation grants | Account namespace; monotonic owner epoch; unique process/generation UUID; DB lineage; lease based on DB time; unarmed/draining/active state |
| Policies, config versions, sessions | Immutable version/hash; original deadline/timezone semantics; session epoch; no restart-based deadline extension |
| Intents, exact wire payloads, send attempts | Durable intent/attempt/operation IDs; permanent scoped wire-ID tombstones; policy and owner binding; legal transitions including `SEND_UNKNOWN` |
| Reservations, capital claims, balances, positions | Exact units and finite decimal amounts; shared spot/SWAP capital; no over-allocation under concurrent commands |
| Fills, fees, cashflows, bill approvals, attribution | Scoped exchange identities; signed fees; causal ordering; exactly-once financial application by immutable operation identity |
| Loss/subsidy/stress budgets, markout/carry state | Existing accounting semantics; gross spending and unresolved obligations survive restart; no automatic refund on an exception |
| Quote actions, hedge/inventory state, joint receipts | Existing ownership, retry/turnover caps, `APPLYING` recovery and fresh-observation bindings |
| Recorder provenance, events, import runs, audit checkpoints | Executor UID/wire scope; append-only history; source hashes; staged import; independently checked projection totals |

**Table needs checked for the ACK increment (2026-10-12):** production migrations
currently contain lineage metadata only. PG2 needs additional financial tables;
the spike tables are fixtures and must not be adopted by startup migration.
Candidate spot layout, to finalize with remaining PG1 command contracts:

| Proposed table | Purpose / mandatory keys and checks |
| --- | --- |
| `accounts` | Environment/exchange/UID/risk-pool/lineage binding, serialization row, owner generation and current versions |
| `sessions` | Immutable session/epoch/config binding and original deadlines |
| `send_attempts` | Exact payload/intent/wire identity, prepare/arm/ACK state, scoped unique exchange order ID; preserve terminal identities |
| `reservations` | Principal and separate LIFE/USDT fee holds linked to the exact attempt; qualified release only |
| `balances` | Exact asset units, current funds/holds and version guards |
| `budget_buckets` | Session/day/campaign limits, committed/actual subsidy and execution-loss projections |
| `operations` | Permanent scoped command fingerprint/receipt and unknown-COMMIT readback |
| `financial_events` | Immutable ordered observations/application facts, including ACK, fills, signed fees and cashflows; independently deduplicated exchange identities |

An ACK does **not** require a standalone `acknowledgements` SQL table: the intended
account transaction updates `send_attempts` and appends the operation/event.
The file adapter uses coordinator ACK records for its multi-journal saga. Further
fill/fee/cashflow, recorder, ownership/grant and enabled SWAP/shared-capital tables
remain under the schema families above. No financial migration is added in this
increment: complete command semantics, precision/scale, FKs, transition guards,
index scopes and actual PostgreSQL constraints/tests remain PG1/PG2 work.

Use PostgreSQL `NUMERIC` with a deliberately selected precision/scale and Python `Decimal`; reject nonfinite values and out-of-range/over-scale input instead of silently rounding an import. PostgreSQL permits special numeric values, so application and database checks must reject them for financial fields. [Numeric types](https://www.postgresql.org/docs/current/datatype-numeric.html).

All authority reads use the writable primary. Read replicas, reporting caches and stale snapshots cannot authorize trading or release reservations.

### Integrity trust model

Current exact-file-byte verification is backend-specific. Do not claim it is preserved by a row version or a checksum stored beside mutable data. Define approved guarantees for stale writers, malformed rows, event/projection divergence, accidental corruption, unauthorized application writes and privileged database tampering.

Require least-privilege roles, immutable event application, version/constraint checks, complete cold replay, and independent audit/checkpoint evidence. If privileged history mutation must be detected before every send, design an independent trusted anchor and prove its latency/availability; an occasional audit or a colocated hash cannot satisfy that contract. PG0 must approve any change from current corruption-detection semantics before PostgreSQL release eligibility. No historical byte-check test may simply be deleted as a performance optimization.

## 4. Non-negotiable financial and sender invariants

1. A durably committed exact intent, reservation/capital hold and armed send-attempt state precede external I/O. Local database success is not an exchange ACK.
2. An ambiguous COMMIT or send outcome remains unknown. Read back by stable operation/attempt identity from the primary; never infer failure, refund a budget, allocate a new wire ID or blindly resend because a connection raised.
3. Financial commands are idempotent by operation ID. Serialization/deadlock retries rerun the complete database command with a bounded policy, never the exchange request. PostgreSQL serializable transactions require retry handling. [Transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html).
4. All allocating writes validate account, policy/config, current owner epoch, observation freshness and row versions atomically. Lock accounts in a single documented order; test concurrent spot/SWAP allocation.
5. Session expiry, HALT, invalid updates, stale market/account facts, loss limits and financial uncertainty still revoke create/amend authority at the actual connector boundary, including retries and queue/throttler waits.
6. Release requires the existing complete exchange, fill/fee/bill, financial, runner and ownership evidence. A cancellation ACK, database row, or lease expiry alone cannot release a hold.
7. Unknown sends, terminal identities, fee/cashflow deduplication, spent budgets, joint exposure and cold-rearm latches survive migration, crash and backup restore.
8. DB outage or pool exhaustion closes normal trading admission and cannot create unbounded queues. Read-only exchange reconciliation and a separately tested emergency cancellation path remain schedulable. Its identity source is a bounded immutable cancellation manifest of durably committed attempt/ACK identities, bound to account, instrument, exchange `ordId`, permanently scoped client/wire ID, generation and DB lineage. It is not a cached trading permit. Missing/ambiguous identity, including an unknown send without qualified exchange `ordId`, is ineligible for this exception. It cannot create/amend, perform account-wide cancellation/CAA renewal, cancel successor orders, bypass credential revocation, release holds, or grant ownership. Uncertain successor activity denies the action and retains the declared outage exposure. The authoritative unknown attempt/hold remains in PostgreSQL; loss of this disposable manifest cannot erase it.

## 5. Ownership: database fencing and exchange fencing

**A PostgreSQL lease/advisory lock does not fence an OKX request.** An old process can pause after its final epoch check, lose its DB session, then resume network I/O using a still-valid exchange key. PostgreSQL advisory locks are cooperative; session locks end with the session. [Advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).

One desired worker instance and revision routing are operating settings, not proof of a single authorized sender. Do not derive exchange ownership from them. Cloud Run service instance limits can briefly be exceeded; worker/revision replacement must also be treated as potentially overlapping execution. [Instance limits](https://docs.cloud.google.com/run/docs/configuring/max-instances), [runtime contract](https://docs.cloud.google.com/run/docs/container-contract).

### Initial supported protocol: manual cold handoff

1. Every process starts with a new process UUID, **read-only/unarmed**. A previously armed revision, secret or restored DB row cannot automatically arm a replacement process. An activation grant binds exactly one process, generation, owner epoch, account, image, config and DB lineage. Only the separate operator/control-plane role can issue/rearm a grant; the runtime role cannot create, relabel or extend its own grant.
2. Mark the current generation `DRAINING`, close normal admission and checkpoint all prepared/unknown attempts. Stop its worker, but do not treat a stop request or lease expiry as termination proof.
3. Revoke the old generation's **exchange write credential**, covering REST and private-WS authority. Removing Secret Manager access or deleting a secret does not revoke an already cached OKX key.
4. Verify revocation effectiveness and the treatment of delayed/in-flight requests using supported exchange behavior. Requests accepted before revocation may still create orders. A control-plane timestamp alone does not prove the drain is complete.
5. Only after old-sender exclusion is established, issue a replacement credential to the new generation in `RECOVERY_ONLY`. This is a process/generation-bound, deny-by-default application grant checked at each actual request boundary for authenticated reads and qualified exact-order cancellation, not an assumed exchange-provided cancel-only key. Create/amend, CAA renewal and rearm remain denied; this grant cannot itself authorize reservation release, which still requires the separate complete financial/reconciliation command. Reconcile account-wide enabled-product orders, accepted/unknown attempts, fills, fees, bills, balances and positions. Cancel only orders with qualified ownership; retain all unresolved holds.
6. Record the handoff evidence and require explicit rearm after matching O/D/R/C gates and fresh financial checks. If old-sender exclusion or reconciliation cannot be proven, the successor remains unarmed, with manual downtime.

Use a **different service identity and secret resource for each trading generation**, with resource-scoped access. An old identity must not fetch the replacement key, impersonate its service identity, or create a new exchange key. Each automatically replaced process still needs its own activation grant; it must not reuse the previous process's grant. The first release therefore requires an operator-controlled new generation after an unexpected trading-worker restart.

Normal create/amend/cancel and CAA updates are generation-bound. The narrow emergency cancel exception in Section 4 must be proven unable to affect successor orders; it is not general permission after owner loss. Expiry headers can limit delayed requests, and exchange cancellation timers can reduce residual risk, but neither establishes ownership. OKX `clOrdId` uniqueness is among pending orders, not permanent exactly-once delivery. Qualify the applicable REST/WS behavior in demo before using it. [OKX API](https://www.okx.com/docs-v5/en/).

Automatic live takeover is outside the first release. A future credential-owning execution gateway is a separate architecture decision and needs its own enforceable takeover; it cannot merely relocate the same lease race.

## 6. Phased implementation and completion checklists

For every checklist: record the failing selector and baseline hash, implement the smallest correction, rerun Green and relevant negative cases, then record commands/results and source/schema/config/image hashes in this plan. Per the operator request, do not create additional evidence files; keep raw test logs in temporary output directories. Existing evidence stays intact. Planned financial/backend test filenames below do not exist yet; migration tests are implemented as listed at the end of this plan. A written design or passing mock is not completion evidence.

### PG0 — Architecture, feasibility, economics and trust decisions

Dependencies: none; can proceed while existing O.8 work continues offline.

- [ ] PG0.1: Freeze declared spot/SWAP/inventory scope, source baseline and open O.8 failures; identify affected A23/A24/A28/A30/A33/A35/A36/A39 contracts.
- [ ] PG0.2: Approve storage/integrity trust model, database transaction/isolation policy, backend-neutral bindings and the manual cold-handoff protocol. Record the external-send fencing limitation explicitly.
- [ ] PG0.3: After the minimum PG0.2/PG0.4 decisions and explicitly synthetic PG0.5 fixture values, spike the actual V2 tick/filter/dispatch → OrderExecutor → awaited database authorization → post-throttler OKX check, plus recorder paths against real PostgreSQL. Prove bounded queue/backpressure, stable action identity, cancellation scheduling, expiry/stop during DB waits and no delayed background sends after stop. The spike is disposable and has no live authority.
  - [x] **Local spot transaction/index experiment (2026-10-12):** real PostgreSQL account-row serialization, current counters, durable attempt/hold/events, scoped identity uniqueness and partial active index; runtime event-update/delete denial; concurrent allocation, retry conflicts, lost replies/ACKs and retained unknown holds. Actual V2 filter → OrderExecutor hook → synthetic bounded async adapter → RESTConnection is exercised with a fake transport/throttle and a spike watchdog. The [code and reproduction](../../test/hummingbot/strategy_v2/life_liquidity/postgres_spike/README.md) and [evidence](LIFE_POSTGRES_SPIKE_EVIDENCE_2026-10-12.md) record 27 functional + 3 scale passes, constant 18 authority SQL statements/10 returned rows per successful send at 0/1k/10k, and passing frozen latency limits in this reduced workload. No full file/PG financial equivalence or O.8 closure is claimed.
  - [x] **Offline spot runner/connector/recorder mechanism (2026-10-12):** actual tick/queue/listener/filter/orchestrator/executor control, OKX protected scheduling/create/tracking/authentication/throttler and REST boundary now carry an optional awaited final check followed by the synchronous local fence. A transactional `READY` operation, actual SQLite recorder projection, fresh metadata reads and `APPLIED` publication gate arm/send. Bounded admission, stop/create priority, late recorder completion, missing/wrong recorder, unknown ACK/cold objects and cancellation during another DB wait pass in the synthetic coordinator. The [assembly evidence](LIFE_POSTGRES_ASSEMBLY_EVIDENCE_2026-10-12.md) records 282 regressions and 13 functional + three enlarged scale passes, with constant 24 PG statements/14 rows per send and corresponding terminal histories in both stores. This closes the stated mechanism only.
  - [ ] **Remaining PG0.3 release assembly:** approved PG0 trust/ownership/packaging inputs; real LIFE controller and backend-neutral ports with production watchdog/config/session lifecycle; all declared enabled product/retry routes; complete recorder repair/outbox crash/restart proof and financial equivalence. The experimental coordinator, synthetic market/account facts and in-memory HTTP session grant no production authority. The awaited DB gate plus final local fence does not establish an atomic DB/send boundary or exclude an old credential-bearing sender.
- [ ] PG0.4: Select and pin a Cloud SQL-supported PostgreSQL major, migration tool and driver compatible with the existing Python/SQLAlchemy runtime; test dependency resolution and Linux image build.
  - [x] **Local migration foundation (2026-10-12):** central versioned SQL runner using optional `asyncpg==0.31.0`; tested on pinned PostgreSQL 16.15 and the current Python 3.13 environment. The [migration guide](../../migrations/life_liquidity/README.md) records command, role, transaction and retry contracts. It follows the separate command/scripts pattern reviewed in local `life-wallet-backend`; adds a checksum ledger and concurrent-run lock instead of re-executing every file. No full SQLAlchemy dependency/image or Cloud SQL connectivity qualification is claimed.
- [ ] PG0.5: Record numeric test/deployment values and rationale for pool size/overflow, query/lock/transaction timeouts, retry caps, lease/heartbeat, observation age, shutdown budget, DB storage/headroom, RPO/RTO and audit lag. Record units, owner and calibration method; synthetic values are not live defaults.
- [ ] PG0.6: Produce a dated VM-versus-Worker-Pool cost/operations comparison and a go/no-go decision for each milestone. Check documented target-region capabilities, account API-key generation/revocation constraints, fixed-egress needs and demo support; keep empirical environment behavior explicitly open for PG9.

**Done when:** decisions and runnable spike results exist, unresolved live inputs are listed, and no implicit live defaults or unsupported fencing claim remains. This does not enable trading.

### Offline technical decisions for PG1 (2026-10-12)

These are engineering assumptions adopted for offline implementation under the current task authorization. They do not approve a change to the live trust model, grant trading authority, choose a deployed topology or close the PG0 parent. The spot preparation/read slice is now implemented against actual legacy journals. Remaining financial command/product ports will be designed against the complete requirements before their schemas are added.

| ID | Offline decision | Live/remaining gate |
| --- | --- | --- |
| PG-D01 | Contract qualification begins with a synthetic spot/account checkpoint; the full declared spot/SWAP/shared-capital/hedge/carry/inventory scope is retained. HEAD `a5257452802f8ea7c1227f9a9773cbfb7c5694ca` plus the current working tree is the baseline; O.8.4/.7 stay open. | All enabled financial/product routes, recorder repair and existing O.8 acceptance remain mandatory. |
| PG-D02 | Binding includes environment, underlying exchange `okx`, authenticated account UID, risk pool, lineage UUID, schema version and exact policy/config hashes. Synthetic UID/hash values are explicitly test-only. A cut has resource versions, revision/event position and snapshot digest; it never grants send permission. | Authenticated UID, actual policy/config provenance, primary/schema/lineage checks and grant binding are qualified in controller/PG integration. |
| PG-D03 | File adapter atomic scope is one `PolicyState` checkpoint. Its UoW can stage multiple logical resources inside that checkpoint. Existing separate financial files remain separate; the new legacy spot adapter declares a recoverable multi-journal saga with durable coordination/readback barriers. Unsupported PostgreSQL selection fails. | Domain-specific repositories, controller path removal and a real account-wide PostgreSQL UoW are required. No implicit atomicity across legacy files. |
| PG-D04 | Proposed PostgreSQL financial commands use short primary `READ COMMITTED` transactions with account-row locking in deterministic account order, atomic expected-owner/row-version checks and DB-time guards. Exchange requests stay outside transactions; only complete storage commands may have bounded retries. | Constraint/concurrency/serialization tests must prove this policy for all enabled commands in PG2/PG3; migration code does not select financial isolation. Escalation to serializable requires full-command retry qualification. |
| PG-D05 | File tests retain the existing `PolicyState` verification model. Proposed PostgreSQL integrity relies on restricted runtime writes, constraints, append-only financial identities and independently qualified cold replay/audit. A colocated checksum/row version cannot attest privileged historical mutation. | PG0.2 live trust decision and PG3.6 audit/anchor proof stay open. No existing mandatory historical verification test is removed. |
| PG-D06 | Owner facts have process/generation/session UUIDs, epoch, explicit UTC validity and mode. Synthetic ACTIVE fixtures may be created only in `OFFLINE_SIMULATION`; default creation is unarmed. Snapshot/owner data is not an activation grant. | Real account lock/lease/grant issuance, new-process unarmed startup, operator rearm and exchange credential exclusion remain PG1/PG4/PG9 work. |
| PG-D07 | One dedicated file worker owns `PolicyState`. Explicit admission/capacity limits, immutable DTOs and operation readback cover queued/cancelled/ambiguous writes. Synthetic test values: 2 admitted operations, 1 MiB checkpoint, 16 retained operation IDs; engineering-owned fixture capacities, not calibrated live values. | File-lock/disk latency is host-dependent. Production queue/watchdog/shutdown bounds, numeric DB pool/retry values, economics and live calibration remain PG0/PG1.3/PG7/PG8. |

### PG1 — Backend contracts and asynchronous integration seams

Dependencies: PG0.

- [ ] PG1.1: Add typed storage/UoW/identity/ownership ports and backend selection; file backend remains explicit and behaviorally compatible.
  - [x] **Checkpoint contracts/file primitive foundation:** immutable `StateBinding`, `StateCut`, `ResourceVersion`, `OwnerToken`, ownership/snapshot/receipt DTOs; async state/owner-read/UoW protocols; explicit backend selector; `PolicyState` adapter with a single checkpoint atomic scope, permanent operation deduplication, fresh owner/cut checks and bounded dedicated worker admission. PostgreSQL selection rejects without file fallback. The [storage guide](../../hummingbot/strategy_v2/life_liquidity/storage/README.md) describes the implemented scope and limitations.
  - [x] **Spot preparation/read domain slice:** typed session/balance/budget/reservation/send-attempt DTOs and async ports with finite Decimal units, operation/wire/quote-slot identity and explicit LIFE/USDT fee buffers. `LegacySpotStore` binds actual SessionStore/WAL/reservation/subsidy/loss files, holds one account UID lock and uses one bounded worker. It declares recoverable saga scope, records PREPARING/ARMING before multi-file work, verifies complete holds before publication, retains unknown/partial work and starts cold read-only. No generic financial checkpoint writes or implicit file/PG fallback.
  - [x] **Spot ACK observation storage slice:** typed immutable ACK command/receipt and readback port; exact prepare/intent/wire/market/side/session/epoch checks, permanently deduplicated observation operations and scoped exchange-ID conflicts. `LegacySpotStore` persists ACKING → actual WAL ACKED → verified completion, retaining principal/fee/subsidy holds on crash/cancellation. Late same-writer observations survive expiry and DRAINING/RECOVERY_ONLY without rearm; cold restore and replaced/unarmed writers cannot complete work. Authenticity and qualified recovery grants belong to the remaining bridge/service, not this DTO.
  - [x] **Spot complete fill/signed-fee and loss/subsidy storage slice:** explicit immutable cumulative snapshots and bound reference-skew policy; finite LIFE/USDT signed fees, independent fill-time values, exact known trade/fee/value identity and no omitted fills. Native reservation fill+fee checkpoints, per-fill execution loss and cumulative subsidy floor publish behind FILLING → APPLIED. Actual fee debits consume matching fee buffers without refunding rebates; overruns close new-risk admission. Fills before ACK bind its later exchange identity; late/over-budget fills are accounted without terminal release/rearm. Source authenticity, full NAV and controller/recovery integration remain below.
  - [x] **Approved spot cashflow and recorded NAV slice:** immutable opening-capital/time and bill approval policy; exact signed LIFE/USDT transfer, bound independent transfer valuation, permanent bill/operation identity and CASHFLOWING → native transfer → verified APPLIED saga. Existing CapitalLedger causal ordering/formulas verify actual balances, cashflow-adjusted NAV, execution loss and starting inventory attribution. NAV/highwater publish atomically in one coordinator checkpoint and survive cold restart; stale clocks, changed approvals/opening capital, unapproved facts and unfunded causal replay are denied. Financial changes invalidate old measurement fingerprints; transfer amounts do not reset budgets or release holds. Source authentication/history completeness and complete recovery/control-plane remain below.
  - [x] **Qualified spot order terminal/exact-operation resume storage slice:** explicit bound freshness policy and immutable exact order/account/fill/fee/bill/runner qualification facts. WAL observation, reservation release, WAL terminal and verified zero-fill subsidy settlement complete behind TERMINATING → TERMINAL, with durable principal overlay and fee claims retained until the final receipt. Partial/full fills retain economic subsidy floors/reservations and loss spending. Exact pending terminal operations can resume with fresh complete facts and the same bound writer on cold read-only stores; no rearm/ownership transfer/resend or repair of other partial commands. Actual authenticated bridge/history/credential qualification remains below.
  - [ ] **Remaining financial commands/application:** qualified filled inventory/exit subsidy settlement, general prepare/arm/ACK/fill/cashflow recovery, stress/markout application, SWAP/shared-capital/recorder ports, actual authenticated ACK/fill/bill/valuation/terminal bridge, approval policy updates/grant/import services and controller backend selection. Archived formats/import and full financial parity remain unqualified. The generic aggregate and legacy saga do not establish account-wide SQL atomicity.
- [ ] PG1.2: Remove direct `Path`/file-byte/internal-cache assumptions from controller installation and financial proof callers; reject backend/account/policy/lineage mismatch instead of fabricating a compatible path.
- [ ] PG1.3: Refactor the synchronous runner scheduling boundary and authority/sender chains to use a bounded async dispatch task; test post-throttler final authorization and every retry for both spot and SWAP, including expiry/stop/config changes while DB work is pending and late task completion after shutdown.
- [ ] PG1.4: Contract-test expiry, stale state, immutable read results, legal transitions, bad config, missing state and independent final-send checks against the file implementation first.
  - [x] **Qualified terminal/exact-resume cases:** zero/partial/full fills, unknown-before-ACK terminal, exact payload/account/liability/fill/bill/runner/owner/cut/freshness checks, replay/conflicts/permanent wire tombstones, native WAL/reservation/subsidy no-op denial, pending BUY/SELL principal overlay, filled economic hold/loss preservation, late expiry, unrelated partial-operation denial, cancellation/account exclusion and killed processes at principal/subsidy/receipt stages. Exact cold resume completes the pending release without granting normal admission. No actual exchange/history authentication or general recovery closure is claimed.
  - [x] **Spot cashflow/NAV cases:** signed transfers in both currencies; approved amount/time/currency/bill bindings; replay and same-bill deduplication; immutable policy/opening capital; deposits excluded from profit; withdrawal/highwater/starting inventory semantics; existing fill loss and budget preservation; stale/foreign ownership and regressing valuation denial; invalidated previous measurements; interruption/native no-op/cancellation/account exclusion; causal underfunding and real killed processes after transfer/NAV commits. These are storage/accounting cases, not authenticated bill coverage or actual independent oracle proof.
  - [x] **Checkpoint contract cases:** binding/owner mismatch, immutable snapshots/staging, stale cut, competing UoWs, idempotent replay and changed-operation conflict, UTC validity/clock rollback/queue expiry, rollback, unknown/cancelled publication with cold readback, file corruption/symlink/missing-state rejection, explicit unsupported backend, admission and retained-ledger/byte capacities.
  - [x] **Spot prepare/arm contracts:** real-journal capital/subsidy/fee claims and replay, insufficient funds/budget, SELL base holds, competing/stale cuts, namespace/owner/session guards, native quote slot/permanent wire identity, cold readback/rearm denial, cancelled worker/account ownership, killed processes after reservation and WAL arm, budget spending/policy preservation. Unknown attempts block new preparation; holds remain through interruptions.
  - [x] **Spot ACK contract cases:** exact/replayed/conflicting identities, same-order duplicate observations, competing ACKs, stale/account/owner rejection, late expiry and observational owner modes, unchanged financial holds/budgets, ambiguous coordinator completion, cancellation/account-lock retention, cold readback and real killed subprocesses before/after actual WAL ACK publication.
  - [x] **Spot fill/fee contract cases:** BUY/SELL, LIFE/USDT fees, actual rebates, partial/full and progressive snapshots, replay/new operation identities, conflict/omission/future/stale/owner/binding/price/quantity/value-skew denial, fill-before-ACK ordering, fee overrun and loss exhaustion, unchanged terminal/owner state, missing native postconditions, interruption/cancellation/account exclusion, policy restore mismatch and killed subprocesses after wallet and subsidy-floor publication. Actual balances/losses agree with the existing accounting formula; this is not full NAV/cashflow/financial-equivalence proof.
  - [ ] **Remaining domain/final-send contracts:** complete authenticated ACK bridge/fill/fee/bill/release/recovery transitions, actual controller config/session/stop lifecycle and independent post-throttler checks for every enabled product. Preparation/arming/ACK receipts do not authorize external I/O.

**Red tests:** `test_storage_contracts.py`, `test_async_final_authorization.py`; delayed/failed DB checks must not block cancellation or leave a cached permission valid.

**Done when:** file regressions pass and the ports can express actual financial transactions without synchronous event-loop SQL, path emulation or reduced authorization.

### PG2 — PostgreSQL schema, transactions and runtime access

Dependencies: PG1 for financial schema/repositories; generic migration infrastructure can precede PG1. Recorder groundwork may proceed once schema decisions are stable.

- [ ] PG2.1: Implement versioned forward migrations and separate migration/runtime/reporting roles; runtime cannot alter schema or rewrite immutable financial history. Prove the chosen permissions with the actual database.
  - [x] **Migration runner and metadata-only bootstrap:** `make life-migration-status` / `make life-migrate`, ordered immutable SQL, applied `(version,name,SHA-256)` ledger, dedicated primary connection and bounded session advisory lock, one transaction per script plus its ledger row. Repeated `up` skips applied versions; changed/missing/renamed/reordered history fails before pending SQL. Errors/cancellation/timeouts roll back the current script; prior versions remain. Fresh-run history readback handles a committed-but-lost reply. TLS verification is default; local insecure mode is explicit and loopback-only. Auto migration is off by default; the explicit `LIFE_AUTO_MIGRATE=true` startup option uses the same runner before application construction. No automatic downgrade, financial state bootstrap or production role provisioning.
  - [ ] **Financial/recorder schema and production privileges:** deliver PG1/PG2.2 schemas, migration/runtime/reporting grants and startup compatibility checks; qualify immutable financial history permissions, image packaging, SQLAlchemy integration and deployed identity/TLS. Fixture role denial tests do not close these requirements.
- [ ] PG2.2: Implement the schema families in Section 3 with account/environment-scoped keys, unique operation/wire/event identities, finite decimal checks, transition/version constraints and indexed active-state queries. Include atomic expected-owner/epoch/process/lineage checks and DB-time expiry guards in every financial UoW; PG4 later qualifies grant issuance and handoff.
- [ ] PG2.3: Add async bounded pools and explicit primary/TLS/account/lineage/schema validation. No replica authority, unlimited overflow, session sharing between tasks or long-lived idle transactions.
- [ ] PG2.4: Test two concurrent allocators, conflicting row versions, deadlock/serialization retries, pool exhaustion, statement cancellation, dropped connections and unknown COMMIT outcomes using real PostgreSQL processes.
- [ ] PG2.5: Qualify durability settings and declared failure domain; `synchronous_commit` configuration alone is not a promise of zero loss for every Cloud SQL failure/PITR scenario.

**Red tests:** `test_postgres_schema.py`, `test_postgres_transactions.py`, `test_postgres_pool_safety.py`.

**Done when:** migrations and all constraints work on the pinned version, conflicts cannot double-spend, uncertain commits read back idempotently, and DB failure closes admission without starving safety work.

### PG3 — Financial authority and durable send attempts

Dependencies: PG2.

PG3 tests seed explicitly synthetic ownership/grant rows through test-only setup roles; real transaction guards remain mandatory and have stale-owner negative cases. There is no runtime ownership bypass. PG4 qualifies actual lease/grant lifecycle and external sender handoff; PG3 completion alone establishes no production ownership safety.

- [ ] PG3.1: Port sessions, WAL/intents, reservations, actions and capital claims with atomic command boundaries and durable pre-send attempt state.
- [ ] PG3.2: Port fills, signed fees, bills/cashflow approvals, attribution, loss/subsidy/stress budgets and markout state; retain permanent identity deduplication and current accounting semantics.
- [ ] PG3.3: Port enabled SWAP/hedge/carry/inventory/joint recovery, including causal same-time fills and interrupted `APPLYING` checkpoints. Explicitly disable every unported product/mode.
- [ ] PG3.4: Implement durable recorder/outbox projection operations and completeness checks; an outbox dispatcher never blindly replays an unknown exchange submission.
- [ ] PG3.5: Kill subprocesses before/after intent/hold commit, armed attempt commit, external request, ACK capture, fill/bill application, projection update and terminal release. Restore fresh objects and prove no blind resend, double accounting or premature refund/release.
- [ ] PG3.6: Verify event/projection invariants and corruption detection under the PG0 trust contract, including cold replay and independently anchored evidence where required.

**Red tests:** `test_postgres_financial_commands.py`, `test_postgres_send_crashes.py`, `test_postgres_joint_recovery.py`.

**Done when:** equivalent file/PG replays yield identical units, balances, holds, budgets, identities and decisions for supported scope; database atomicity is never presented as exactly-once exchange delivery.

### PG4 — Ownership, grants and enforceable handoff

Dependencies: PG3; fake exchange and local processes only until the later demo gate.

- [ ] PG4.1: Implement DB-time lease, account-row serialization, monotonic owner epoch, process/generation UUID, lineage and one-use activation grants; stale writers cannot change financial state.
- [ ] PG4.2: Implement startup unarmed, `DRAINING`, `RECOVERY_ONLY` and explicit rearm states; expiry/session loss does not activate a successor.
- [ ] PG4.3: Implement generation-specific credential/service-identity access and manual handoff evidence; model key revocation delay and accepted in-flight requests without assuming instant exclusion.
- [ ] PG4.4: Pause sender A after its final DB check, expire/disconnect A, start B, then resume A. Demonstrate the unfenced lease-only failure as a retained Red reproduction; the supported protocol must keep B unarmed until A's external write authority is excluded.
- [ ] PG4.5: Test simultaneous revisions, same-revision replacement, lost heartbeat, DB failover/clock anomalies, stale activation grants and attempted replacement-secret access by the old identity.
- [ ] PG4.6: Test all write routes including amendments, retries, cancellations and CAA, and the deny-by-default `RECOVERY_ONLY` allowlist. Prove any outage emergency cancel uses the Section 4 durable identity manifest, rejects missing/ambiguous/foreign identities and cannot touch successor activity. Test DB unavailability during/after handoff with an old process attempting cancel: revocation is never bypassed, uncertain successor activity denies, and losing the manifest cannot release DB holds.

**Red tests:** `test_postgres_ownership.py`, `test_sender_generation_handoff.py`, `test_generation_secret_isolation.py`.

**Done when:** local multi-process fault tests pass and automatic live takeover remains disabled. Actual OKX credential exclusion is still a PG9 environment gate; local mocks do not close it.

### PG5 — Hummingbot recorder and operational compatibility

Dependencies: PG2/PG3; can overlap PG4.

- [ ] PG5.1: Configure and qualify PostgreSQL recorder access; replace local-file schema migration assumptions with versioned SQL migrations for the supported tables.
- [ ] PG5.2: Preserve executor config/custom-info, authenticated/configured UID distinction, active and historical wire IDs, positions, trade/fee precision and cold-restart provenance.
- [ ] PG5.3: Make recorder writes/queries bounded and independently scheduled; if a synchronous recorder adapter remains, use a bounded serialized worker with task-local sessions and explicit failure/backpressure behavior. Never pass live sessions or authority objects across threads.
- [ ] PG5.4: Update CLI status/history/trades and recovery queries that assume a local `.sqlite` path; surface backend health and incomplete provenance accurately.
- [ ] PG5.5: Test real orchestrator store/read/restart against PostgreSQL, missing/foreign/legacy rows, commit/read failure and delayed recorder projection; retain claims and executor retry state appropriately.

**Red tests:** `test_postgres_recorder_restart.py`, `test_postgres_cli_history.py` plus existing recorder/orchestrator recovery suites.

**Done when:** actual recorder/runner integration works without a hidden SQLite authority fallback or a false recovery-complete verdict.

### PG6 — Import, backups, restoration and rollback

Dependencies: PG3–PG5.

- [ ] PG6.1: Add offline import for complete legacy and segmented file cuts plus SQLite recorder provenance. Stop the file writer, acquire existing account ownership, validate every source digest/policy/binding and freeze an immutable source manifest.
- [ ] PG6.2: Import into `IMPORTING` staging with resumable batches and stable source operation IDs. Preserve original deadlines/epochs, all terminal tombstones, unresolved sends/holds, fees/cashflows/approvals, spent budgets and interrupted joint/action state.
- [ ] PG6.3: Compare exact financial totals, complete identity sets and recovered decisions; activate the imported lineage atomically and **unarmed**. Missing/corrupt source evidence blocks activation; never initialize empty authority to work around an import failure.
- [ ] PG6.4: Keep the source immutable and non-sending. Before any DB-owned orders, reversal can discard staging. After DB-owned orders, rollback requires reconciled export/cutover and a new fenced generation; toggling the backend flag is prohibited.
- [ ] PG6.5: Exercise backup/PITR restore into an isolated database. Restore is unarmed, has a new controlled lineage, rejects stale grants/epochs, and requires old-credential exclusion plus exchange history reconciliation since the restored cut. No empty history or replayed budget reset.
- [ ] PG6.6: Record measured backup restore, import, reconciliation, retained history and settlement/recovery headroom against PG0 RPO/RTO/capacity decisions. Export schema/versioned audit evidence; do not rely on backup existence alone.

**Red tests:** `test_postgres_import.py`, `test_postgres_restore.py`, `test_postgres_cutover_rollback.py` with killed/resumed importer and fresh processes.

**Done when:** both source formats import exactly, repeated import is idempotent, restorations cannot rearm, and the rollback runbook has a passing rehearsal.

### PG7 — Portable container and Cloud infrastructure definitions

Dependencies: PG0 decisions; worker code depends on PG1–PG5. Definitions can be prepared offline; provisioning belongs to PG9.

- [ ] PG7.1: Build a pinned Linux image from this repository with PostgreSQL dependencies; run foreground, receive signals directly, emit structured stdout/stderr, and keep credentials outside image layers/arguments/logs. Do not use the destructive current `make build` target.
- [ ] PG7.2: Validate startup without TTY/prompts/MQTT dependency, schema/account binding before authority construction, and no auto-bootstrap of missing financial state. Ephemeral local files may hold disposable scratch/config/status only.
- [ ] PG7.3: Add infrastructure definitions for Artifact Registry, Cloud SQL PostgreSQL, backup/PITR settings, VPC, IAM, Secret Manager, monitoring and one desired Worker Pool instance per account namespace. **Operator-selected flow: config-enabled startup migration** (`LIFE_AUTO_MIGRATE=true`); package the pinned SQL bundle/driver and await the central runner before application construction, with no manual migration step. Database locking serializes concurrent startup runners; failed migration blocks that startup. Qualify migration/runtime role and secret isolation: removing an inherited DSN does not prevent a colocated process from reading a mounted env file/secret. A separate serialized migration Job is an optional deployment mode (`LIFE_AUTO_MIGRATE=false`), with migration-only identity/secret, one task, parallelism one and automatic retries disabled; await success before worker rollout. Runtime startup validates compatibility and remains unarmed; incompatible schema changes require an explicit drain/upgrade policy in either mode. [Cloud Run Jobs](https://docs.cloud.google.com/run/docs/create-jobs), [job retries](https://docs.cloud.google.com/run/docs/configuring/max-retries).
- [ ] PG7.4: Define same-region worker/primary DB, private-IP/TLS connectivity with Direct VPC egress and bounded pools; test TLS/authentication handling locally. Document the selected Worker Pool authentication/connection method; its actual connectivity is a PG9.2 gate, not inferred from a Service-only example. [Direct VPC](https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc), [Cloud SQL connectivity](https://docs.cloud.google.com/sql/docs/postgres/connect-run).
- [ ] PG7.5: Define fixed egress IP via VPC/NAT if required for exchange allowlisting, including every REST/WS write path and an explicit verification procedure. Actual egress/key restrictions are measured in PG9.2/PG9.4. [Static outbound IP](https://docs.cloud.google.com/run/docs/configuring/static-outbound-ip).
- [ ] PG7.6: Define scoped generation identities/secrets, separate migration/runtime/reporting privileges and no old-to-new impersonation path; pin secret/image/config versions. Validate policy definitions and local negative access fixtures; actual deployed access is mandatory in PG9.2/PG9.4. [Worker Pool secrets](https://docs.cloud.google.com/run/docs/configuring/workerpools/secrets).
- [ ] PG7.7: On SIGTERM close admission first, checkpoint attempted/unknown work, initiate bounded owned cancellations and exit within a configured budget below Cloud Run's 10-second termination window. Forced kill must preserve unknowns; shutdown completion does not imply exchange terminality. [Shutdown contract](https://docs.cloud.google.com/run/docs/container-contract).
- [ ] PG7.8: Add metrics/alerts for trading eligibility, owner generation, DB/query/pool lag, event-loop lag, unknown attempts, unresolved holds, audit freshness, budget usage and backup/recovery health. Probe/liveness success is separate from permission to trade; a healthy unarmed process must not restart-loop because it awaits manual recovery.

**Done when:** image and IaC validate locally, missing secrets/DB fail closed, ephemeral-directory deletion preserves authoritative state, and container signal/forced-kill tests pass. Cloud deployment is still unproven.

### PG8 — Complete PostgreSQL offline acceptance and performance

Dependencies: PG1–PG7 local work; no cloud/OKX credentials required.

- [ ] PG8.1: Run shared backend contract tests and actual V2 runner/executor replays against a real pinned PostgreSQL container, including supported spot/SWAP/inventory scope and a fake exchange.
- [ ] PG8.2: Require PG crash/concurrency/lease/unknown-COMMIT/import/restore/recorder/corruption cases in an executable acceptance map with test selectors, owners, source/schema hashes and explicit unresolved environment proofs.
- [ ] PG8.3: Retain every existing mandatory O.8 variant/outcome and add PostgreSQL at 0/1k/10k complete settled history using the actual sender/watchdog. Keep frozen limits: poll 2,000 ms, proposal/filter/send 1,000 ms, maximum loop lag 500 ms, cancel start 250 ms; budgets SHA-256 `e1be70f238efdaad68086e3ae43fdc8ca4447a4c6da6dc36c03260ca7e6be5c3`.
- [ ] PG8.4: Measure bounded query/row/byte counts, active versus total history, transaction latency, DB CPU/I/O/storage/WAL, worker RSS, connection count and cancellation delay. Run timeouts and pool contention with adverse concurrent work; no hidden full-history materialization on each permission check.
- [ ] PG8.5: Record complete repeated-run results on a declared resource profile, with profiling separate from latency gates and no overlapping measurements. Retain every failed/interrupted sample; do not widen ceilings or select only successful runs.
- [ ] PG8.6: Require constraint/invariant parity, full regression and the existing pinned coverage/completion gates plus new PG gates. Keep old evidence immutable. A new backend does not silently waive a failing mandatory legacy variant; changing supported scope requires a separately reviewed, versioned decision and acceptance map.

**Done when:** the declared product's existing offline gate and the new PostgreSQL gate both close with complete evidence. A passing PG-only suite cannot close O.8.4/O.8.7 by itself. Further cloud network performance remains PG9.

### PG9 — Cloud sandbox, VM comparison and OKX demo

Dependencies: PG8 and the matching existing O gate closed. Requires isolated nonproduction infrastructure/accounts and the applicable demo authorization. PG0 declares `postgres_vm` or `postgres_cloud_run` as the release topology. The VM milestone needs PG9.1–PG9.6 on the VM plus applicable portable/database/IAM/recovery work from PG7; Cloud Run-specific PG7 work and PG9.7 remain explicitly deferred/open if Cloud Run is not selected. Claiming Cloud Run readiness requires those additional items. This is a scope distinction for the new deployment extension, not a waiver of existing O.8 requirements.

- [ ] PG9.1: Provision an isolated VM/database sandbox from reviewed IaC and qualify the PostgreSQL worker there first. Record image/config/data shape, resources, network and actual cost inputs for later comparison.
- [ ] PG9.2: Prove startup, private DB connectivity, fixed egress where needed, IAM/secret isolation, logs/alerts, backup/restore and resource limits in the deployed environment.
- [ ] PG9.3: Exercise real container replacement, overlapping processes, SIGTERM/SIGKILL, DB disconnect/failover and recovery on the selected topology. Every replacement is unarmed until its own qualified generation handoff; one desired instance is not the assertion under test. Cloud Run scope additionally requires overlapping revisions and same-revision replacement.
- [ ] PG9.4: Use isolated OKX demo credentials and supported instruments to qualify send/retry/cancel/WS behavior, key revocation propagation, old-key REST/WS denial, accepted-before-revocation requests and the complete manual handoff. Unsupported demo credential controls remain unresolved, not simulated passes for live eligibility.
- [ ] PG9.5: Repeat the complete 0/1k/10k deployed performance matrix and a calibrated longer session with growing history, loss budgets, fills/fees/bills, carry, expiry and cold restart. Record throughput, latency, memory, storage growth and recovery downtime under the declared configuration.
- [ ] PG9.6: Compare the measured VM/Cloud Run outcome against PG0 economics and operating criteria. Choose the production topology explicitly; keep PostgreSQL-on-VM supported if Cloud Run offers insufficient benefit.
- [ ] PG9.7: For `postgres_cloud_run`, provision the Worker Pool and its Cloud-specific PG7 resources from reviewed IaC, then run PG9.2–PG9.5 on the same image/config/data shape. Require real revision/replacement/credential-isolation proof and a dated VM-versus-Cloud Run comparison before Cloud Run eligibility. If this experiment is deferred, record Cloud Run costs as estimates and outcomes as unmeasured; PG9.6 may select only the qualified VM topology.

**Done when:** deployed environment and demo evidence pass for the declared topology/scope; Cloud Run-specific work remains open for a VM-only milestone. A non-LIFE demo instrument proves its connector/deployment behavior; synthetic LIFE replay and actual LIFE availability remain separate. No production order permission is granted.

### PG10 — Production read-only qualification and canary review

Dependencies: applicable PG9 topology gate plus the existing R/C requirements as reached below. Choosing VM does not bypass them; a VM milestone does not claim Cloud Run deployment completion.

- [ ] PG10.1: Qualify authenticated production account, actual instruments/fees/history, opening balances, bill anchors, reference quality and explicit numeric economic/stress budgets using read-only access first.
- [ ] PG10.2: Freeze source/image/schema/IaC/config/credential-generation bindings; close all PG0 live decisions and qualify production ownership exclusion/handoff prerequisites with provider-qualified REST/private-WS revocation/drain evidence. Revocation timestamps, IAM denial or a read-only authentication probe alone do not prove write exclusion, and demo eligibility does not establish production revocation behavior. If hard-fence proof requires a mutating production probe, specify a separately authorized bounded validation/cleanup step under the existing R/C approval policy before activation; absent that authorization or sufficient non-mutating proof, the gate stays open and trading remains unarmed.
- [ ] PG10.3: Produce a concrete rollback/recovery runbook, current account/order exposure report, handoff evidence and budgeted canary proposal for separate explicit approval under the existing C gate.
- [ ] PG10.4: Only after approval, run the bounded live canary and record actual costs, latency, restart/reconciliation and liquidity KPIs before increasing capital. Remaining unknowns retain holds and stop expansion.

**Done when:** approved canary evidence passes and the operator accepts the measured topology. Deployment success, a DB migration or this plan cannot close the trading release gate.

## 7. Acceptance map and delivery evidence

| Case | Required result | Phases |
| --- | --- | --- |
| PC01 Concurrent spot/SWAP allocation | Exactly one valid allocation outcome per command; no double-counted capital | PG2/PG3 |
| PC02 Ambiguous COMMIT | Stable operation readback; no new ID/refund/blind send | PG2/PG3 |
| PC03 Crash around external send/ACK | Unknown attempt and holds survive; complete reconciliation precedes release | PG3/PG6 |
| PC04 Duplicate/out-of-order fills, fees and bills | Exact financial application once; causal ambiguity blocks | PG3 |
| PC05 Paused stale sender resumes | Successor stays unarmed until external exclusion; lease-only failure retained | PG4/PG9 |
| PC06 Replacement secret/identity access | Old identity cannot obtain replacement write authority | PG4/PG7/PG9 |
| PC07 DB outage/pool saturation | No normal trading; bounded proven cancellation and unresolved holds remain | PG2/PG4/PG8 |
| PC08 Legacy/segmented import | Exact complete identity/financial parity; activation unarmed | PG6 |
| PC09 Backup/PITR restore | New controlled lineage, no stale grant/rearm, history reconciled | PG6/PG9 |
| PC10 Growth to 10k history | Unchanged O.8 budgets pass; complete query/resource evidence | PG8/PG9 |
| PC11 SIGTERM then forced kill | Admission closes, unknown state survives, restart unarmed | PG7/PG9 |
| PC12 Recorder missing/foreign/delayed row | Recovery incomplete; no premature release or dropped executor | PG5 |
| PC13 History corruption/projection mismatch | Denied under the approved PG0 trust contract; independent audit evidence | PG2/PG3/PG8 |
| PC14 Cloud topology economics | Dated comparison and explicit go/no-go, including manual handoff downtime | PG0/PG9 |
| PC15 Actual account/canary | Existing R/C gates remain mandatory | PG10 |
| PC16 Migration replay/concurrency/drift | Applied versions skipped, script+ledger atomic, two jobs serialized, role/timeout/drift failures block deployment | PG2/PG7 |

Each phase checkpoint in this plan must include commands/selectors, Red and Green outcomes, real PostgreSQL version, source/schema/config/image hashes, environment/resource profile, complete failures/skips, and remaining gates; no additional evidence file is required. Classify every skip; an unavailable cloud/demo dependency is an open gate. No credential values or secret payloads belong in evidence.

Artifact locations (migration infrastructure exists; other components remain proposed):

- `hummingbot/strategy_v2/life_liquidity/storage/`: migration runner plus checkpoint DTO/protocol/file adapter implemented; spot read/preparation ports and actual legacy saga adapter implemented; remaining financial commands and controller/PostgreSQL account UoW integration pending.
- `test/hummingbot/strategy_v2/life_liquidity/storage/`: migration validation/real-PG and checkpoint contract tests implemented; spot real-journal preparation/crash cases implemented; full financial/product backend contract/fault suites pending.
- `migrations/life_liquidity/`: metadata-only `000001_storage_lineage.sql` and migration guide implemented; financial/recorder migrations and release compatibility policy pending.
- `deploy/life_liquidity/`: portable image entrypoint and reviewed VM/Worker Pool infrastructure definitions.

The migration CLI/Make targets above are available. Other proposed commands/resources must not be assumed implemented. Per the operator request, this migration increment creates no new evidence report/artifact: test summaries are recorded in this plan and raw logs/XML stay in `/tmp`.

### Migration test checkpoint (2026-10-12)

- Red: migration validation initially failed collection because the storage module was absent (`/tmp/life-pg-migrations-red.xml`).
- First Green: 21 validation cases and 16 real PostgreSQL integration cases passed (`/tmp/life-pg-migrations-unit-green.xml`, `/tmp/life-pg-migrations-integration-20261012-first/`). Additional TLS, nested transaction, DDL-lock and caller-transaction negative cases were then added for the final check below.
- Final Green: **26 validation tests + 19 real PostgreSQL integration tests passed**, no skips/failures. Commands: `python -m pytest -q test/hummingbot/strategy_v2/life_liquidity/storage/test_migration_files.py` and `python -m test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations --output /tmp/life-pg-migrations-integration-20261012-final`, run inside the existing `hummingbot` environment with the isolated optional driver on `PYTHONPATH`. Unit XML: `/tmp/life-pg-migrations-unit-final.xml`; integration XML/log/version: the final output directory. Integration includes nested COMMIT rejection, bounded DDL-lock failure and denial of an enclosing caller transaction. Container: PostgreSQL 16.15, image `postgres@sha256:e17e86066e5ef83e0952a9347f5c792b7ece00972e2aa787a6986f471b3dd3d5`, 2 CPUs/1 GiB; Python 3.13.15, asyncpg 0.31.0. The disposable container was removed.
- Final migration bundle SHA-256: `hummingbot/strategy_v2/life_liquidity/storage/postgres/migrations.py`: `3a8ca25bb8bfd49158ff65b914307a9eb0cbe74e53f0f3745edfaf509fc77f1d`; `migrations/life_liquidity/000001_storage_lineage.sql`: `20ddeb99cd6496d28988e3d0f616fff3998c35d53877c3d8293ba467c3db86e3`; `setup/life_postgres_requirements.txt`: `2d3ab3c26147a027196223201940abe98598de500b73178e40952440c4c418a2`. These identify this migration increment; the larger dirty working tree remains outside its qualification scope.
- Formatting/security hooks and `git diff --check` passed. An initial hook run found one unused import, indentation and an image-digest false positive; these were corrected before the final tests. These are migration infrastructure tests, not PG financial correctness or trading eligibility tests.


### Optional startup/env configuration (2026-10-12)

- [x] Versioned scripts remain separate files under `migrations/life_liquidity/`, currently `000001_storage_lineage.sql`; future scripts use the same six-digit increasing prefix. Do not rename an applied file merely to change numbering style.
- [x] Add root `.env.example` and create a private, gitignored `.env` only when absent. Existing local files are preserved. Default `LIFE_AUTO_MIGRATE=false`, empty DSN, TLS verification, explicit numeric migration timeouts. The same loader supports manual CLI and startup, OS-over-file precedence, literal parsing, strict booleans/timeouts, alternate/disabled env file and safe errors.
- [x] Shared application bootstrap (quickstart/detached engine) and the legacy interactive launcher await enabled migrations before creating the application/connectors; failure/cancellation blocks startup. Optional mode creates no financial state and does not select the PostgreSQL runtime backend.
- [x] Final verification: 87 tests passed in the combined migration validation/config/startup + existing runner/engine regression suite (`/tmp/life-pg-auto-migration-regression.xml`). The four-case launcher suite then passed including an additional legacy interactive startup case (`/tmp/life-pg-auto-migration-launchers.xml`; three cases overlap the combined suite). All 21 real PostgreSQL integration cases passed, including disabled/enabled/repeated auto migration and dotenv-only CLI (`/tmp/life-pg-auto-migrations-integration-20261012/`), on the same pinned PostgreSQL 16.15 container; the container was removed. No failures/skips; existing Pydantic deprecation warnings remain. Hooks and `git diff --check` pass. Initial Red was missing config-module collection (`/tmp/life-pg-auto-migration-red.xml`); the first config/file Green had 42 passes (`/tmp/life-pg-auto-migration-unit-green.xml`). Logs stay in `/tmp`; no evidence file is created.

Migration/env startup source SHA-256: `hummingbot/strategy_v2/life_liquidity/storage/postgres/config.py`: `36ff7f669cbd7dee398e0bd37c761214edf0d15a95e046f6467d9456c6c582f6`; `hummingbot/strategy_v2/life_liquidity/storage/postgres/migrations.py`: `f3723da650d26f7cd526ebdc7ba0ffba470507ee0e3ca7808e5999ef0cfb058b`; `hummingbot/client/runner.py`: `55448e4fabc5cb5e5d5a8baac8bc6ef3930bab7afeda51dceab46044a98927d5`; `bin/hummingbot.py`: `c991090c4eb69f8d1dc1cfb9a16731b7695541dbb85eeb5d243b745f30f8e592`; `.env.example`: `1532ef04df8fc961fd8d37831b0fb9850a55147a38b43b78f9d2c9aa9e41a100`. Local `.env` values are excluded from this record.

### PG1 checkpoint test record (2026-10-12)

Initial Red: `/tmp/life-pg1-contract-red.xml` failed collection because contract modules were absent. First implementation: 25 passed / 1 failed (`/tmp/life-pg1-contract-first-green.xml`); the failure exposed incompatibility with the existing immutable list subclass and was fixed by preserving its sequence semantics. The next scoped run passed 34 cases (`/tmp/life-pg1-contract-green.xml`). Expanded regression passed **167 tests**, no failures/errors/skips, with existing Pydantic warnings (`/tmp/life-pg1-file-regression.xml`): checkpoint contracts, migration file/config tests and existing policy/archive/WAL/reservation suites. Raw logs stay in `/tmp`; no evidence file is created. The expanded final gate then passed **173 tests, including 45 checkpoint contract cases**, no failures/errors/skips (`/tmp/life-pg1-file-final.xml`), adding invalid UTC/owner epochs, DRAINING/RECOVERY_ONLY denial and cancelled-close cleanup. Commands: `conda run -n hummingbot python -m pytest -q` with `storage/test_storage_contracts.py`, `storage/test_migration_files.py`, `storage/test_migration_config.py`, `test_policy_state.py`, `test_checkpoint_archive.py`, `test_archived_journals.py`, `test_reservation_persistence.py`, `test_reservations.py` and `test_wal.py` under `test/hummingbot/strategy_v2/life_liquidity/`. No PostgreSQL or account from `.env` is accessed by these suites. Formatting/security hooks and `git diff --check` passed; initial protocol one-line/blank-line style findings were fixed before the final gate. No performance/O.8 or production eligibility conclusion is drawn.


Checkpoint contract source SHA-256: `hummingbot/strategy_v2/life_liquidity/storage/contracts.py`: `029044256d2f88c2fdbfed8be844dfe73e1552232bef755cd130d722c943e4ce`; `hummingbot/strategy_v2/life_liquidity/storage/file.py`: `d5bbdbe5e3d8e3a7d882c85a82c1f7782bc05f0d4f481ff1a56f7ddd760b3c99`; `test/hummingbot/strategy_v2/life_liquidity/storage/test_storage_contracts.py`: `e160f9902452320524dabac2c15e91798485e45fed77e6da5851bf42082a3201`; `hummingbot/strategy_v2/life_liquidity/policy_state.py`: `c3b27543919c2f3d788ecc6b2bf775f861f6a00bcf21e6a2d9b333eb598e635c`. File format is `storage_contract=1` inside the existing `PolicyState` schema 1 envelope; synthetic capacities are recorded in PG-D07.

### PG1 spot financial contract checkpoint (2026-10-12)

- [x] Source slice and limitations recorded in the [storage guide](../../hummingbot/strategy_v2/life_liquidity/storage/README.md). Current HEAD is `a5257452802f8ea7c1227f9a9773cbfb7c5694ca` plus working tree; source hashes follow the final check. No financial SQL migration or controller activation occurs.
- [x] TDD Red: absent financial modules (`/tmp/life-pg1-spot-red.xml`). First scope: 16 passes (`/tmp/life-pg1-spot-first.xml`). Expanded real-journal/kill-process cases: 23 passes (`/tmp/life-pg1-spot-expanded.xml`). Pre-fee-buffer regression: 233 passes with existing Pydantic warnings (`/tmp/life-pg1-spot-regression.xml`). Final fee/encoding/slot additions: 31 passes (`/tmp/life-pg1-spot-fee-final.xml`). No skips/failures in these Green runs; original Red remains in `/tmp`.
- [x] Final combined regression: **234 passed, including 31 new spot financial cases; 0 failures/errors/skips**, with existing Pydantic deprecation warnings (`/tmp/life-pg1-spot-final-regression.xml`). Command: `conda run -n hummingbot python -m pytest -q` over the storage directory and existing WAL/session/reservation/persistence/policy/archive/economics/loss/subsidy suites under `test/hummingbot/strategy_v2/life_liquidity/`. Tests include actual killed subprocesses after reservation and WAL arm, cancelled worker ownership, stable Decimal representation, quote/base fee funding and no implicit cold rearm. Formatting/security hooks and `git diff --check` pass; initial indentation findings were corrected. Financial state/commands remain offline; no exchange requests or configured `.env` DB are accessed. No evidence file is created.


Spot financial contract source SHA-256: `hummingbot/strategy_v2/life_liquidity/storage/contracts.py`: `dac65da2864feb5fe5fb558eb2a53d6c9a7956c11b929963b0a23ab739ea88ff`; `hummingbot/strategy_v2/life_liquidity/storage/financial.py`: `de732b1ae903d3639c2e354b61e9c740c2a4e5302f259e9d8ad4199942061488`; `hummingbot/strategy_v2/life_liquidity/storage/legacy_spot.py`: `05f03a1716fe8be770ffee3a00a2853fcdae6452d8da309580e03a2468bc5104`; `test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_financial_contracts.py`: `720b6bf442ed139f5f7802329dc09f0701dc24637e4e207c0bce38357bb05b92`. The legacy coordinator uses generic checkpoint schema 1 and the bound legacy policy; no SQL schema/driver/image changed. Capacities and balances are synthetic as documented in the storage guide.

### PG1 spot ACK observation checkpoint (2026-10-12)

Baseline: HEAD `58840a29f` plus the uncommitted slice below. Synthetic fixture
bindings/balances/capacities remain those in `test_spot_financial_contracts.py`.
No configured `.env` database, real exchange, SQL migration or deployment is used.

- [x] Red: `test_spot_ack_contracts.py` failed collection because `AckSpot` was absent (`/tmp/life-pg1-ack-red.xml`).
- [x] Initial Green: 23 ACK cases passed (`/tmp/life-pg1-ack-first-green.xml`). Expanded ACK/owner-mode/cancellation/real-process-kill cases plus generic checkpoint contracts passed 74 tests (`/tmp/life-pg1-ack-expanded.xml`).
- [x] Combined regression: **263 passed, including 29 ACK cases; 0 failures/errors/skips**, with six existing Pydantic warnings (`/tmp/life-pg1-ack-final-regression.xml`). Command: `PYTHONPATH=. conda run -n hummingbot python -m pytest -q` over the storage directory and existing WAL/session/reservations/reservation-persistence/policy/archive/economics/loss/subsidy suites, with JUnit output in `/tmp`. Formatting/security hooks passed after correcting fixture imports/indentation; the 29 ACK cases were rerun after formatting (`/tmp/life-pg1-ack-post-format.xml`). No standalone evidence file is created.
- [x] Final ACK postcondition guard: an adapter cannot publish success unless actual WAL state and exchange ID match ACKED. A synthetic native no-op retains SEND_UNKNOWN and the recovery barrier. The same combined command passes **264 tests, including 30 ACK cases; 0 failures/errors/skips**, with existing warnings (`/tmp/life-pg1-ack-complete-regression.xml`); changed-file hooks pass.
- [x] Table needs recorded in Section 3: future account/session/attempt/reservation/balance/budget/operation/event tables. ACK uses attempt + operation/event rows; no standalone ACK SQL table or premature financial migration is required by this storage increment.
- [ ] Remaining PG1 financial commands and authenticated controller/recovery bridge; PG2 financial migrations/constraints and PostgreSQL adapter; O.8/performance/demo/live qualification. The file adapter still verifies retained journal history and no performance gate is closed.

ACK source SHA-256: `storage/financial.py`: `65a471aa2d4756c4c310ab14163b46bfa0c71bb88d66dc0fbedf1374135379eb`; `storage/file.py`: `bd3ed5b9c9a9d74af8c889334b0c6115ad06c41ac8ac056de0d6a8841746594a`; `storage/legacy_spot.py`: `446a3e9030c4efa1156f6e97f16ca658a7a4e2b74338806aec201d4071d6e461` (all under `hummingbot/strategy_v2/life_liquidity/`); `test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_ack_contracts.py`: `45821d3947822801790447512357c148783f5f61ad3e306015ab7b2f66edac9d`. No SQL/driver/image changed.

### PG1 spot fill/fee loss/subsidy checkpoint (2026-10-12)

Baseline: HEAD `58840a29f` plus the previous uncommitted ACK slice and current
changes. Values and UIDs remain explicitly synthetic: `SpotFillPolicy(1000 ms)`,
the existing 5 LIFE/10 USDT balances, nested 10/5/2 USDT budgets and file capacities.
No exchange, configured `.env` database, production schema or image is accessed.

- [x] Red: missing `FillSpot` failed collection (`/tmp/life-pg1-fill-red.xml`). Initial Green passed 28 fill cases (`/tmp/life-pg1-fill-first-green.xml`); expanded fill + ACK suite passed 66 (`/tmp/life-pg1-fill-expanded.xml`).
- [x] Final combined regression: **326 passed, including 38 new fill cases and the existing 24 reconciled-attribution tests; 0 failures/errors/skips**, with six existing Pydantic warnings (`/tmp/life-pg1-fill-final-regression.xml`). Command: `PYTHONPATH=. conda run -n hummingbot python -m pytest -q` over the storage directory plus existing WAL/session/reservations/reservation-persistence/policy/archive/economics/loss/subsidy and `test_reconciled_fill_attribution.py` suites. The 38 fill cases also pass after formatting (`/tmp/life-pg1-fill-post-format.xml`); changed-file hooks and `git diff --check` pass after correcting continuation indentation. No evidence file is created.
- [x] Filled quantities and signed LIFE/USDT fees apply once in the actual native reservation checkpoint; consumed fee buffers do not double-hold money or credit rebates. Per-fill independent execution loss and cumulative subsidy floors retain existing formula/bucket semantics, including over-budget actual observations. Fee overruns close new-risk admission. Full fills remain nonterminal with unresolved fee/subsidy/identity holds.
- [x] Partial publications retain FILLING and proposed facts; cancelled workers retain account exclusion. Fresh subprocesses reopen after real process exits immediately after wallet or subsidy-floor checkpoints without refunding or replaying writes. No power-loss durability or completed cold recovery service is claimed.
- [ ] Remaining cashflow-approved/full NAV/causal attribution and qualified terminal/recovery commands, authenticated bridge/controller wiring, complete financial equivalence and PostgreSQL financial migrations. O.8/performance remains open: the adapter still scans retained file/projection history.

Source SHA-256 (under `hummingbot/strategy_v2/life_liquidity/`): `storage/financial.py`: `f82ac882f6968add350bfa4b3c7b1ca04a329f8c0091f80f36e6918e96cd056e`; `storage/legacy_spot.py`: `e3d44809c887faf3fd4802ee03b130007e5c769ce90256ed7c858da47c2a1cd5`; `storage/file.py`: `4bcd6b6775ce8a2868167276d398dbc911f3b1e051fbaa096adc8e51171d6c7a`; `test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_fill_contracts.py`: `9a117ab05549c063937b9708edc2d46a7bc2f9e268ca01eba11f42bef7684bac`. No SQL/driver/image changed. The optional fill policy is explicitly bound in file coordinator metadata; previous coordinators require a qualified import to adopt it.

### PG1 approved cashflow and recorded NAV checkpoint (2026-10-12)

Previous completed auto-migration/ACK/fill slice committed as `f98ff2e43`
(`feat(life): add replayable spot ACK and fill accounting`); the capital slice
below is a new working-tree change. Opening 5 LIFE/10 USDT at 1 USDT/LIFE,
fixed numeric bill approvals/times and 1000 ms value skew are synthetic fixtures,
not operator approval, authenticated bills, live oracle or calibration defaults.

- [x] Red: absent `storage.capital` failed collection (`/tmp/life-pg1-capital-red.xml`). Initial Green passed 22 cases (`/tmp/life-pg1-capital-first.xml`). Expanded capital/fill suite passed 66 (`/tmp/life-pg1-capital-expanded.xml`); extended capital cases passed 30 (`/tmp/life-pg1-capital-extended.xml`).
- [x] Combined regression: **370 passed, 0 failures/errors/skips**, including the 30 capital cases then present, existing storage/WAL/session/reservations/persistence/policy/archive/economics/loss/subsidy/reconciled-fill/reconciled-cashflow/accounting cases; six existing Pydantic warnings (`/tmp/life-pg1-capital-regression.xml`). Command: `PYTHONPATH=. conda run -n hummingbot python -m pytest -q` over these explicit suites under `test/hummingbot/strategy_v2/life_liquidity/`, with JUnit output in `/tmp`.
- [x] Final capital suite **31 passed**, including the added regressing NAV clock guard (`/tmp/life-pg1-capital-final.xml`); changed-file formatting/security hooks and `git diff --check` pass. This final selector verifies the added guard; the 370-case result is the preceding combined checkpoint. No new evidence file, configured `.env` DB, SQL migration, exchange request or deployment is used.
- [x] Transfers preserve native funds, immutable bill identities and existing budgets. Timestamp-ordered CapitalLedger projection cannot fund earlier withdrawals with later fills. Recorded net-cashflow-adjusted NAV/highwater and starting inventory/loss measurements survive restart. Fixed approvals/opening values cannot be silently changed on restore. Negative spendable funds deny healthy snapshots and leave proposed transfer facts/actual debit/holds for recovery; no automatic balance repair or refund.
- [x] Real subprocess exits after native transfer and committed NAV, plus cancellation/account exclusion and missing native postconditions, retain readback and cold-unarmed mode. NAV `current` compares financial facts only; price freshness/trading permission and complete venue coverage require the pending bridge.
- [ ] Qualified terminal/recovery and approval-update/import services, authenticated account-wide bill/fill/oracle coverage, joint funding/SWAP/recorder/markout, controller ports, PostgreSQL financial transactions and full equivalence remain open. O.8 performance/power-loss durability/live gates remain unchanged.

Source SHA-256: `hummingbot/strategy_v2/life_liquidity/storage/capital.py`: `a605c1847374562c87ae55680fdc5a5f7c6f82699353368a2519ef5694943bc1`; `hummingbot/strategy_v2/life_liquidity/storage/legacy_spot.py`: `114e4646ef39936fdadc3617777782ff2c36fdf69e43f4256992235b9170a8b8`; `test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_capital_contracts.py`: `4c3a7bfda25caa3e9cbaf00c358be34f900090f4e09d94e2f1b532c684cb59e0`. Optional accounting policy is bound to the existing file coordinator policy. No SQL/driver/image changed.

### PG1 qualified terminal and exact-resume checkpoint (2026-10-12)

Baseline: HEAD `8312fe806` (`feat(life): add approved spot cashflows and persistent NAV accounting`)
plus this new working-tree slice. Synthetic fixture freshness is explicitly
`SpotTerminalPolicy(1000 ms)` with the existing capital/fee/budget/account fixtures.
Qualification flags/facts are synthetic assertions; they are not authenticated
OKX history, independently sourced balance/valuation or a live ownership grant.

- [x] Red: terminal fixture setup failed on the absent `terminal_policy` API (27 errors, 8 DTO shape passes; `/tmp/life-pg1-terminal-red.xml`). Initial Green passed 35 cases (`/tmp/life-pg1-terminal-first.xml`). Expanded crash/cancellation cases exposed an incomplete test checkpoint fixture version; the fixture was corrected before Green. Native publication postconditions and complete terminal projection checks remain enforced.
- [x] Final regression **463 passed, including 50 new terminal cases; 0 failures/errors/skips**, with six existing Pydantic warnings (`/tmp/life-pg1-terminal-regression.xml`). Command: `PYTHONPATH=. conda run -n hummingbot python -m pytest -q` over the storage directory and existing WAL/session/reservations/reservation-persistence/policy/archive/economics/loss/subsidy/reconciled-fill/reconciled-cashflow/accounting and `test_order_gateway.py` suites. Changed-file formatting/security hooks and `git diff --check` pass; raw logs remain in `/tmp`, no evidence file is created.
- [x] Native `SubsidyBudgetLedger.matches_settled` verifies actual cost/session rather than treating a missing reserve as successful settlement. Zero-fill terminal refunds unused subsidy; partial/full fills retain economic subsidy floors/reservations and execution loss. Unused order principal and fee claims release only on completed terminal receipt. Quote slots become reusable while permanent intent/wire/exchange identities remain.
- [x] Real killed processes after principal release, zero-fill subsidy settlement and final coordinator receipt restore pending principal overlays or completed readback. Cancelled workers retain account exclusion until physical completion. Fresh exact-operation resume finishes one pending saga without owner transfer, rearm or resend; cold-unarmed admission stays denied. This is single-host storage recovery, not external credential fencing or power-loss durability.
- [ ] General preparation/arm/ACK/fill/cashflow recovery and control-plane grants/approval updates/import; filled inventory/exit economic settlement; authenticated gateway/bill/runner history integration; SWAP/shared-capital/recorder ports; PostgreSQL transactions and full equivalence. Existing O.8/demo/live gates remain open. No configured `.env` database, SQL migration/driver/image change, exchange request or deployment is used.

Source SHA-256 (under `hummingbot/strategy_v2/life_liquidity/`): `storage/terminal.py`: `0fc8e84cb9626168132df6b086c00be5bf92453a08c9a123ebc5756f7c8aa2a9`; `storage/terminal_adapter.py`: `dd768d1e01634fe6b7e35f0df40102673d82fd944211645bd4c3ecf9edaf24f3`; `storage/legacy_spot.py`: `b207a0e5f7c316a902d86647243c848b7dc55e54b45975453ec8039dcb41c097`; `economics.py`: `dd1f4b740a249770ad451b9fb3b1f821cc0847bda496e00a616ecf2c2b041a63`; `test/hummingbot/strategy_v2/life_liquidity/storage/test_spot_terminal_contracts.py`: `88f3fda3db3213ffb328ee1443b178722f6d35f34c93609615d321d759a619d1`. Terminal freshness policy is bound to the existing coordinator policy; old coordinators require qualified import to adopt it.

## 8. Suggested execution order

1. Use the central migration runner for all new LIFE PostgreSQL DDL. The operator-selected `LIFE_AUTO_MIGRATE=true` service startup and optional manual/Job modes share the same version/checksum/lock contract. Package SQL/driver and qualify deployment secrets before cloud startup automation. Finish the remaining PG0 decisions and define PG1 backend contracts before adding financial schema/authority, while preserving current O.8 results.
2. Implement PG1–PG6 offline against real local PostgreSQL; prepare PG7 container/IaC without provisioning.
3. Close PG8 and the matching existing offline gate. Keep the declared mandatory legacy requirements unless an explicit scope amendment is approved.
4. Qualify PostgreSQL on a VM, then evaluate Cloud Run in PG9 sandbox/demo using the same worker build.
5. Proceed to PG10 production read-only qualification and the separately approved existing R/C canary on the topology whose applicable PG9 gate has closed. Cloud Run additionally requires PG9.7 ownership/deployment proof and favorable economics. If it is deferred, the qualified VM may proceed through PG10/R/C while Cloud Run-specific items stay open; no production approval is bypassed.

The immediate next task is **define/implement remaining general spot recovery and qualified filled inventory/exit economic settlement, then PG1.2/PG1.3 authenticated controller/sender/terminal integration using the measured spot assembly**. Offline checkpoint, preparation, ACK, fill/fee loss/subsidy, approved cashflow, recorded NAV and exact-order terminal/resume storage slices are implemented. Broader product/recorder ports and the remaining PG0 live trust/ownership, packaging and calibration decisions stay explicit. The tick/queue/OKX/recorder mechanism now has offline proof in the experimental coordinator; production LIFE authority migration, recorder repair and full financial equivalence remain open. Schedule estimates should follow those bounded integration slices.
