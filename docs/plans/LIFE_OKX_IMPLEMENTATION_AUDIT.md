# LIFE/OKX implementation audit after the PostgreSQL port

Historical scope note (2026-10-25): the current [LIFE/OKX plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
now defines a smaller spot MM/session/admin API MVP. The P0–P9/A01–A39 mappings
below describe the previous scope, not the current delivery checklist. Source
findings remain useful when reusing the corresponding components.

Reviewed: 2026-10-24 (Asia/Ho_Chi_Minh). Source: `2301ef6a6`.
Scope: compare [the LIFE/OKX plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md) with
readable source and reproducible tests. No exchange calls, credentials, demo/live
orders or cloud deployment were used for this audit.

**Conclusion:** the shared PostgreSQL/Cloud Run infrastructure and ordinary OKX
trading paths are implemented. The LIFE coordinating controller, timed sessions,
reference model, economic/risk budgets and reservation ledger are not implemented
as reviewable source in this checkout. No complete LIFE phase or acceptance case
A01–A39 is established by the port's test results.

## Source inventory

`git ls-files '*life_liquidity*' '*life*runner*'` returns no tracked implementation.
`controllers/generic/life_liquidity.py` and `scripts/life_liquidity_runner.py` are
absent. The directories `hummingbot/strategy_v2/life_liquidity/` and
`test/hummingbot/strategy_v2/life_liquidity/` contain 252 and 657 `.pyc` files,
respectively, but no `.py` or `.sql` source. These are residual artifacts, not
reviewable implementations or runnable pytest suites. They were not deleted or
counted as completed work. Old editor tabs naming LIFE SQL files are not evidence
that those files exist in the current checkout.

## Implemented components to reuse

| Component and evidence | What is already available | What it does not establish |
|---|---|---|
| [PostgreSQL runner](../../hummingbot/model/db_migration/postgres/runner.py), [SQL scripts](../../hummingbot/model/db_migration/postgres/scripts), [runtime operations](../postgres-runtime.md) | Canonical schema, idempotent migration, `ENABLE_AUTO_MIGRATION`, schema validation, durable recorder/control state | LIFE session, reservation, budget and economic-audit schemas |
| [Service](../../hummingbot/cli/service.py), [runtime config](../../hummingbot/client/config/runtime_config.py), [environment credentials](../../hummingbot/client/config/environment_credentials.py) | Foreground service, probes, bounded shutdown, local dotenv/cloud environment input, ordinary YAML, credential validation | LIFE mode selection, shadow/demo isolation or its configuration validation |
| [Ownership](../../hummingbot/core/bot_ownership.py), [send gate](../../hummingbot/core/order_submission_guard.py), [REST assistant](../../hummingbot/core/web_assistant/rest_assistant.py) | Dedicated PG session lock/generation; write fencing; final ownership/HALT/control-version checks after throttling/authentication; bounded transport and request deadline | A permit binding LIFE session/config/risk epochs, quantity/price, reservation, data freshness and economics; account-wide exclusion of unrelated deployments using other databases |
| [Recorder](../../hummingbot/connector/markets_recorder.py), [ownership integration tests](../../test/integration/postgres/test_ownership.py), [recorder tests](../../test/integration/postgres/test_recorder.py) | Stable client ID persisted before send; atomic order/status/fill/tracking state; duplicate handling; lost-ACK reconciliation; rollback latch and durable HALT | Atomic LIFE capital reservations, session/day/campaign budgets, slot identity and their release rules |
| [V2 runner](../../scripts/v2_with_controllers.py), [orchestrator](../../hummingbot/strategy_v2/executors/executor_orchestrator.py) | Existing controller/executor lifecycle, restored positions/PnL peaks/stopped-controller state, scoped streaming PnL recovery and in-memory reports | Timed LIFE session recovery; NAV/cashflow accounting; turnover-independent capital drawdown; automatic eligibility to resume after uncertainty |
| [OKX spot](../../hummingbot/connector/exchange/okx/okx_exchange.py), [perpetual](../../hummingbot/connector/derivative/okx_perpetual/okx_perpetual_derivative.py), existing order/position/TWAP executors | Books, balances, order/fill tracking, tick/lot handling, spot/perpetual placement and `LIMIT_MAKER` → `post_only` mapping | Complete listing-phase, account-fee, close-only, shared collateral or LIFE-specific risk contracts |
| [Independent offline exchange](../../test/e2e/offline/exchange.py), [E2E runner](../../scripts/test_e2e_offline.py), [verification report](../postgres-verification.md) | Real image/connector/engine/PG flow; V1 spot and V2 spot/perpetual; faults, restart and distinct-image rollback | LIFE instruments/session logic, joint spot/perpetual coordination, conservative queue/impact economics, calibrated backtests or real OKX demo evidence |

Extend these components. Do not build a second trading engine, migration framework,
ownership system or recorder. Cache optimization is already reviewed separately;
Redis is not a prerequisite for the LIFE work.

## Status by phase

“Partial foundation” means reusable generic behavior exists, not that the phase's
LIFE acceptance conditions pass.

| Phase | Current status | Additional implementation/evidence required |
|---|---|---|
| P0 | Partial; P0.4 and P0.7 audited | R01–R12/test mapping; LIFE fake-clock fixtures; explicit state/permission specification; economic objective, datasets and decision register. Reuse the existing simulator and ledger contracts. |
| P1 | Not implemented for LIFE | `LifeLiquidityConfig`, duration parser, units, mode gates, atomic versioned changes, safe examples/schema, CLI discovery. Existing Pydantic validators and controller update utilities are building blocks. |
| P2 | Partial connector foundation | Listing/auction/pre-open gate; per-market readiness; timestamp/sequence/quality contracts; actual fee snapshots; freshness and shared-account snapshot semantics. |
| P3 | Not implemented | Qualified LIFE reference; optional bounded benchmark; persisted start/deadline/anchor; expiry and exactly one successor; own-quote exclusion/divergence checks. |
| P4 | Partial persistence/send-gate foundation | Economics, NAV/cashflows, reservations, fill/loss/subsidy/stress budgets, markouts and hysteresis; full expiring send permits; independent safety loop and cancellation capacity. |
| P5 | Partial generic MM/order lifecycle | LIFE quote generation and per-session level slots; post-only/retry contracts; inventory caps; expiry/pause/replenishment and adverse-fill policies; coordinator integration. |
| P6 | Partial perpetual connector/executor foundation | Correct position conversion and close-only semantics first; coordinated spot/perpetual collateral/exposure; bounded hedge execution, funding/carry and margin stress. |
| P7 | Generic executors only | Inventory target/deadline/price/impact budgets; partial completion; safe MM/inventory transitions and exit attribution. TWAP defaults cannot satisfy this contract unchanged. |
| P8 | Partial generic recovery/E2E foundation | Persist/recover LIFE sessions/reservations/budgets; clock trust; safety-aware exchange timer; decision audit and economic telemetry; LIFE fault/queue/adverse-flow replay. |
| P9 | Partial infrastructure CI/runbook foundation | LIFE simulation/shadow/demo adapters and credential separation; full-session soak; diff coverage; economic baselines/gates; operator decisions and separately authorized canary. No Cloud Run staging is required by the port. |

Requirement mapping: R01/R02/R09/R10 have generic building blocks; R03–R08 and
R11–R12 require the LIFE logic above. Even R01/R02 are not complete LIFE market
making merely because a generic BTC/ETH fixture can place an OKX order.

## Confirmed connector and integration gaps

1. **Spot listing eligibility — P2.1–P2.5.**
   [`okx_utils.is_exchange_information_valid`](../../hummingbot/connector/exchange/okx/okx_utils.py)
   checks instrument type and currencies, but not state or continuous-trading
   phase. A local probe with `state='suspend'` still returns `True`. The perpetual
   filter already checks live linear SWAPs; this is useful but not the full
   listing/auction/reference-quality contract.
2. **Account fees — P2.10/P4.11.** Both `_update_trading_fees()` implementations
   remain `pass`. Generic default fee schedules and recorded fill fees do not
   supply a current account/instrument fee snapshot for pre-send economics.
3. **Perpetual quantity — P6.2 / A31.**
   [`get_position_amount`](../../hummingbot/connector/derivative/okx_perpetual/okx_perpetual_derivative.py)
   uses `notionalUsd / avgPx` and `max(amount, round(amount))`, rather than
   position contract count and instrument metadata. Local probes show `60/100`
   becomes **1**, `avgPx=0` raises `DivisionByZero`, and the net-side parser's
   `int('0.5')` raises `ValueError`. Cover fractional lots, unchanged contracts
   under valuation changes, REST/WS consistency and zero/absent values before
   enabling shared risk calculations. Existing order/fill conversion tests do
   not establish correctness of this separate position path.
4. **Close semantics/margin — P6.1/P6.3 / A32.** `_place_order()` hard-codes
   `tdMode='cross'`; leverage setup uses `mgnMode='cross'`. The ONEWAY branch sets
   `posSide='net'` without `reduceOnly`. The existing HEDGE `posSide` mapping is
   reusable but needs shrinking-position/retry tests; explicit isolated mode
   must not be accepted while requests still use cross.
5. **Safety scheduling and send authority — P4.13–P4.15 / A23–A24/A36.**
   [`ControllerBase.control_task`](../../hummingbot/strategy_v2/controllers/controller_base.py)
   still waits for global `MarketDataProvider.ready` **and** an executor update
   event. The service observer is independent, but does not implement LIFE feed,
   session, reservation or economics checks. Extend the existing final gate;
   don't bypass it with a controller-only approval or a second REST sender.
   No Cancel All After integration was found in the OKX connectors; the PG gate
   currently rejects unreviewed mutation paths, so any timer requires a reviewed
   authorization/scope policy as well as heartbeat code.
6. **Reference, hedging and accounting — P3/P4/P6/P7.** The external price
   delegate returns raw prices; `CustomAPIDataFeed` starts at zero and retains
   old values on fetch failure. `hedge_asset` still uses `asset-USDC` and example
   leverage 20. TWAP defaults to TAKER; its MAKER selects LIMIT, not LIMIT_MAKER.
   V2 `global_pnl_pct` uses turnover, while its quote-PnL peak is not a complete
   capital NAV/cashflow ledger. Reuse these utilities only inside the required
   LIFE contracts.
7. **Funding and demo are partial, not absent — P6.12/P9.3.** Perpetual REST/WS
   funding data already consume exchange `nextFundingTime`. A legacy eight-hour
   helper remains, but no caller was found in the OKX perpetual source; do not
   describe the entire funding feed as hard-coded. Carry budgets, dynamic
   settlement/regime tests and post-expiry monitoring remain missing. Demo
   domain/WS constants exist, but no simulated-trading header was found in the
   reviewed OKX request paths. The cloud gate enables only reviewed `okx` and
   `okx_perpetual` connector identities. End-to-end demo routing/credential
   isolation is not demonstrated by the constants or the offline simulator.

## Acceptance evidence that can be extended

| LIFE cases | Existing evidence | Still open |
|---|---|---|
| A08/A09/A35 | Atomic recorder, duplicate/out-of-order fills, lost ACK, interrupted transaction and known-order recovery | LIFE reservations/slot identities, session epochs, all cancel/replace/force-stop races |
| A14/A23/A28 | Durable HALT, control-version check after throttle, restored strategy PnL peaks/stopped controllers | Session expiry, source quality and economic permits; persistent daily/campaign budgets; hysteresis/probe sizing |
| A31/A32 | Generic perpetual placement/fills and position-hold E2E | Position quantity/close-only gaps above; race with independently shrinking exchange position |
| A39 | Single PG owner/generation and unknown-order reconciliation within the dedicated bot database | Aggregate spot/perpetual assets/reservations; account/risk-pool binding. Independent deployments using different DBs on one account remain unsupported. |
| A04/A10/A13/A24–A30/A33–A38 | Independent simulator and fault-injection infrastructure | LIFE-specific events, joint risk, timer, adverse-flow and economics assertions; existing tests do not close these cases |

All other LIFE acceptance cases remain unimplemented/unverified. Do not copy the
port's checkbox status onto the LIFE matrix: those are different acceptance IDs
and scopes.

## Audit validation and next implementation sequence

New audit run: **387 passed, 7 warnings, no failures/skips**, Python 3.13.15 on
macOS arm64, using the existing Conda/Cython environment:

```sh
/opt/homebrew/Caskroom/miniforge/base/envs/hummingbot/bin/python -m pytest -q \
  test/hummingbot/connector/exchange/okx \
  test/hummingbot/connector/derivative/okx_perpetual \
  test/hummingbot/strategy_v2/controllers \
  test/hummingbot/strategy_v2/executors/order_executor \
  test/hummingbot/strategy_v2/executors/twap_executor \
  test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py
```

Saved evidence: `build/life-okx-audit/regression.log` and `source-probes.json`.
The source probes call only local filter/position conversion functions. They
demonstrate current behavior; they are not implemented LIFE acceptance tests or
a phase's completed Red → Green cycle. Previously completed port evidence remains
926 regression tests, 71 PostgreSQL integration tests and five image E2E scenarios
in [postgres-verification.md](../postgres-verification.md); it was not rerun or
claimed as new LIFE evidence in this documentation audit.

Proceed in the plan's existing order: finish P0's remaining specification,
LIFE fixtures and economic decision register, then P1 → P2 → P3 → P4 → P5.
Use the PostgreSQL runner/recorder/ownership layer from the first persisted session
and intent. Release only the spot scope after its applicable P8/P9 gates; implement
P6 hedging/perpetuals and P7 inventory extensions at their defined stages. Live
input/calibration gaps do not block explicitly synthetic offline implementation.

Keep one coordinator and the original engine. Ordinary YAML stays configuration;
credentials come from the existing environment adapter. No new Redis, deployment
framework, generic cache layer or second recovery system is needed to start.
