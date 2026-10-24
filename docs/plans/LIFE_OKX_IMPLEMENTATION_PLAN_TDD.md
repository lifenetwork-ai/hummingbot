# LIFE / OKX: Market-Making MVP Plan

Updated: 2026-10-25 (Asia/Ho_Chi_Minh). Runtime baseline: `2d1dc0b7b`.
Status: planning; the LIFE scenarios and admin API are not implemented yet.
The [previous audit](LIFE_OKX_IMPLEMENTATION_AUDIT.md) covers historical scope;
this document is the current delivery checklist.

## 1. Required behavior

Run the existing Hummingbot MM engine with three selectable reference profiles:

| Scenario | Reference behavior |
|---|---|
| `follow_market` | Follow the percentage change of a selected pair such as BTC-USDT or ETH-USDT |
| `sideways` | Quote around a saved LIFE anchor using configured spread and quote bounds |
| `follow_token` | Follow a configured token that the operator selects because it is trending |

The two follow scenarios share one calculation. YAML lists the selectable pairs;
each profile follows one pair. These modes control the bot's quotes, not the
resulting market price.

Admin API selects `market_mode: spot | perpetual | both`, profile and duration.
`both` runs spot and perpetual concurrently with the **same scenario and clock**,
using separate market budgets/limits. Random mode selects a scenario/profile at
step boundaries until a finite overall deadline; it keeps the chosen market mode.

Use one service/container, the existing V2 runner, and one shared MM controller
implementation instantiated for each enabled connector (`okx`, `okx_perpetual`).
Reusing V2 covers both markets without adding a separate V1 implementation.

## 2. Existing code to extend

| Component | Work |
|---|---|
| [V2 MM base](../../hummingbot/strategy_v2/controllers/market_making_controller_base.py) and [PMM simple](../../controllers/market_making/pmm_simple.py) | Reuse quote levels, sizing, refresh and executor actions; supply the reference through `update_processed_data()` |
| [V2 runner](../../scripts/v2_with_controllers.py) and existing executors | Load one or two instances; reuse lifecycle, order tracking, position holding and recovery |
| [OKX spot](../../hummingbot/connector/exchange/okx/okx_exchange.py) / [perpetual](../../hummingbot/connector/derivative/okx_perpetual/okx_perpetual_derivative.py) | Reuse books, balances, orders, positions and funding records; fix only the required connector gaps below |
| [aiohttp service](../../hummingbot/cli/service.py) | Add admin routes and independent session/deadline handling on the existing port/owner loop |
| [Control store](../../hummingbot/model/bot_state_store.py), [send guard](../../hummingbot/core/order_submission_guard.py) and recorder | Reuse versioned commands, owner fencing, HALT, database transactions and order/fill persistence |
| Existing YAML/env loaders, SQL runner and offline simulator | Add profiles, minimal current-session/command state and focused tests |

New behavior is limited to reference profiles, timed/random sessions and admin
adapters. Keep current state and retry receipts in PostgreSQL using existing
models/connections and simple idempotent snake_case SQL. Keep configuration in YAML.
No session-history subsystem or order-to-session attribution is required.

## 3. YAML profiles

Package `deploy/cloudrun/conf/life_mm_profiles.yaml` as
`conf/life_mm_profiles.yaml`. Reuse existing controller configuration models for
spread, order size, refresh, inventory/exposure limits and perpetual settings.
The API selects deployed IDs and validated runtime fields; it does not edit YAML.

Illustrative profile fragment, not yet runnable:

```yaml
profiles:
  market_btc:
    scenario: follow_market
    reference_pair: BTC-USDT
  market_eth:
    scenario: follow_market
    reference_pair: ETH-USDT
  sideways_default:
    scenario: sideways
    anchor: session_start_mid
  trend_eth:
    scenario: follow_token
    reference_pair: ETH-USDT
random:
  scenarios: [follow_market, sideways, follow_token]
  step_duration_seconds: 600
```

The complete YAML also supplies market/controller settings, permitted/default
runtime durations and per-market limits. Return these through `/profiles` so FE
uses the same validation rules. Credentials stay in `.env`/injected secrets.

Follow calculation: `reference = LIFE_anchor × source_price / source_anchor`.
Use Decimal; save each enabled market's LIFE anchor and the source anchor before
quoting. Sideways uses its saved LIFE anchor. Source prices come from the existing
market-data provider. Absolute BTC/ETH prices must never become LIFE quotes.

Use a fresh market midpoint for the anchor, or an explicit configured initial
anchor if no usable LIFE midpoint exists. Missing/stale data, non-positive prices
or quote-bound violations block new quotes and cancel outstanding quotes.

## 4. Spot, perpetual and both

- Validate each requested instrument is listed and tradable. Reject an unavailable
  perpetual market; do not silently turn `both` into `spot`.
- Reuse maker execution explicitly. V2 sample defaults include LIMIT entries,
  market exits and automatic market rebalancing; configure the intended MM policy
  instead of inheriting these defaults. Session duration is separate from an
  executor's position `time_limit`.
- Initial perpetual account settings are **CROSS + HEDGE**, with explicit leverage
  and position caps in YAML. Expose supported settings to FE and reject unsupported
  account modes. Do not inherit the example leverage of 20. Changing account mode
  with open orders/positions is outside these scenario controls. Confirm account
  mode and leverage were applied successfully before allowing quotes.
- Fix perpetual base quantity using Decimal contract counts and existing contract
  metadata. The current `notionalUsd / avgPx` plus rounding calculation is wrong
  for fractional amounts and fails at zero entry price. Test REST and WS updates.
- Use separate spot inventory/order and perpetual position/margin caps; bound
  long and short/gross exposure, not only their net difference. In `both`,
  validate the combined declared budgets and available collateral so both sides
  cannot allocate the entire shared balance. Reuse tracked orders/positions and
  available balances; test fills/in-flight orders against these caps.
- `both` starts only when both markets are ready. Failure of either required feed
  or market blocks/cancels quotes on both while preserving the session deadline.
  Report the failing market in status; the operator may switch to `spot` explicitly.
- Stop, expiry and switch cancel/reconcile active quotes and preserve actual spot
  inventory and perpetual positions through existing position-hold/recovery paths.
  Use `keep_position=True` for executor transitions; the default can issue market
  closes. They do not automatically flatten positions. FE continues showing held exposure,
  including on a disabled market; configured loss/exposure checks include it when
  accepting a later session. Reconcile stale held exposure before new trading or
  reject startup. Existing risk HALT remains authoritative.

## 5. Timed and random sessions

Only three commands are needed: **start, switch, stop**. Start/switch specify the
market mode, fixed/random selection and duration. A different duration is an
explicit switch to a new session; pause/resume and in-place duration editing are
not part of the MVP.

- Persist current session ID, configuration hash, market mode, selected profile,
  anchors, start/expiry, current random step and control version before new orders.
- Duration starts when the selected markets are ready. Once started, downtime and
  reconnects do not reset anchors or extend the deadline.
- Switch cancels/reconciles prior quotes before starting the new selection. Stop
  or expiry revokes new sends, including queued executor retries, and cancels quotes.
  Run these checks independently of market-data readiness; the final send guard
  also rejects an old step/profile generation within the same session.
- Random chooses uniformly among configured scenarios, then among profiles within
  that scenario. Use fixed step windows anchored to session start and capped by
  the overall end; each draw is persisted once. Both markets use the same draw.
  Capture new market/source anchors once per new step, after prior quotes are
  cancelled/reconciled; preserve those anchors across restarts within that step.
- On restart, retain a saved current-window draw; skip elapsed windows without
  catch-up orders. Missing data blocks the selected profile's quotes rather than
  causing a redraw. Retry readiness within its window without extending deadlines.
- Ordinary start/switch/random selection cannot clear a recovery HALT or discard
  fills, open positions or unresolved exchange effects.

## 6. Basic-authenticated admin API

Mount `/api/v1/admin` on the existing aiohttp server. All admin reads, writes and
schema access require Basic Auth. Keep existing minimal health probes separate.

Proposed env settings: `ADMIN_API_ENABLED=false`, `ADMIN_API_USERNAME`,
`ADMIN_API_PASSWORD` (no usable credential defaults), `ADMIN_CORS_ORIGINS` (empty
by default). Reuse dotenv/Cloud Run secrets and centralized defaults; no `LIFE_`
prefix. Enabling the API requires credentials. Use constant-time checks, HTTPS in
deployment, `401`/`WWW-Authenticate` for bad credentials and redact auth from logs.
FE sends operator-supplied credentials per request and keeps them out of its build.
Allow only configured cross-origin FE origins/headers; actual requests remain
authenticated after preflight. FE can poll; no new login/token service is needed.

Paths below are relative to `/api/v1/admin`:

| Method / path | Purpose |
|---|---|
| `GET /profiles` | Available scenarios/pairs, market modes, validated settings, defaults and limits |
| `GET /status` | Requested/actual state, per-market readiness, current session/random step, references, expiry/time left, versions, staleness and HALT reason |
| `POST /commands` | Start/switch/stop with validated market mode, selection and duration |
| `GET /commands/{id}` | Accepted/applying/succeeded/failed/superseded result and effective version/session |
| `GET /orders` | Existing bot-owned open/history orders, filtered by market/pair/time |
| `GET /fills` | Existing fills/fees filtered by market/pair/time |
| `GET /balances` | Total/available balances with snapshot freshness |
| `GET /positions` | Actual perpetual positions, leverage/margin data available from the connector, and freshness; visible after quoting stops |
| `GET /performance` | Existing per-market PnL, fees, volume and funding data where available, labeled with valuation basis/time |
| `GET /openapi.json` | Small request/response/error contract with FE examples |

Reuse ORM/snapshots/performance helpers; do not shell out to CLI or promise new
per-scenario accounting. Monetary values are Decimal strings, timestamps UTC and
history reads have bounded pagination. Mark unavailable/stale data explicitly.

Example command body, with Basic Auth and `Idempotency-Key` headers:

```json
{
  "action": "start",
  "expected_version": 12,
  "market_mode": "both",
  "selection": "fixed",
  "profile_id": "market_btc",
  "duration_seconds": 3600
}
```

Random requests use configured scenario/profile IDs, overall `duration_seconds`
and `step_duration_seconds`. Switch/stop identify the current session. Start
requires no active session. Reject arbitrary paths, unknown profiles and unsupported
market/account settings.

Reuse control-version CAS and add a small durable idempotency receipt in the same
transaction. Check matching retries before CAS. A matching retry returns its
original command without resetting time;
a reused key with different data or stale version returns `409`. Return `202` on
acceptance, then let the existing owner apply the request. Completion requires the
actual requested state: the existing acknowledged version alone is not success.
Publish validation (`422`), not-found (`404`) and unavailable (`503`) errors in the
API schema. No second command worker/service is required.

## 7. Implementation checklist

Complete phases in order, update evidence here and commit each finished phase.

### P1 — Reuse V2 for both markets and three profiles

- [ ] Add the packaged profile catalogue using existing config models/loaders; validate supported pairs, market modes, durations and limits.
- [ ] Extend one V2 MM controller with normalized follow/fixed-anchor reference logic; reuse quote/executor lifecycle for spot and perpetual instances.
- [ ] Fix required perpetual quantity handling; explicitly configure maker execution, position holding, leverage and supported CROSS/HEDGE settings.
- [ ] Verify listing/freshness checks, inventory/exposure caps and combined budgets for `both`, including held positions and in-flight orders.
- [ ] Test both connectors, reference units/anchors, invalid inputs and original V2 behavior using focused regressions.

### P2 — Current session and random selection

- [ ] Persist minimal current-session state using existing PostgreSQL/migration/control machinery.
- [ ] Implement start/switch/stop, independent expiry handling and final-send session checks without replacing ownership/HALT/recovery.
- [ ] Add persisted random draws/windows shared by both markets; preserve deadlines across restart.
- [ ] Test fake-clock expiry, market-mode switches, cancel/fill races, restart/downtime, stale feeds and both-market readiness failures.

### P3 — Admin API for FE

- [ ] Add Basic Auth/env validation and routes to the existing aiohttp server, retaining health probes and explicit CORS where needed.
- [ ] Add versioned/idempotent command receipts and observable completion through the existing owner loop.
- [ ] Expose profile/status and existing order/fill/balance/position/performance data, with bounded reads and freshness.
- [ ] Publish the API schema/examples; test auth, malformed input, retries, conflicting versions, failures, decimal output and all three market-mode selections.

### P4 — Offline acceptance

- [ ] Extend the existing simulator with LIFE spot/SWAP and configured reference pairs; exercise the actual V2/OKX/PostgreSQL service.
- [ ] Verify all three scenarios on spot and perpetual; verify `both`, random selection, API mode switches, expiry and restart without losing held exposure or deadlines.
- [ ] Exercise the FE HTTP workflow: catalogue/status, Basic Auth commands, completion polling, stop and order/fill/balance/position/performance reads.
- [ ] Run relevant existing regression and deployment-image E2E gates; document YAML/env setup, API use and recovery, update evidence and commit. Cloud Run staging is not required.

## 8. Scope kept out

Weighted baskets/sensitivity tuning, automatic trend discovery, automatic
spot/perpetual hedging, TWAP inventory execution, pause/resume, live duration
editing, a separate session-history/per-session PnL subsystem, general economics/
reservation frameworks, Redis, WebSockets, user/RBAC management and writable YAML
APIs are outside this implementation. Current session state in `/status` is enough
for FE control; existing order/fill history remains available.

Completion means the three scenarios, timed/random sessions and authenticated FE
controls work on **spot, perpetual and both**, using the existing engine and
verified offline. Existing ownership, persistence, recovery and risk protections
remain required.
