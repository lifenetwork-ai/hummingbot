# LIFE / OKX: Market-Making MVP Plan

Updated: 2026-10-25 (Asia/Ho_Chi_Minh). Runtime baseline: `2d1dc0b7b`.
Status: P1 controller/configuration and P2 session/service integration implemented
and locally tested; P3 admin API and subsequent acceptance phases remain open.
The [previous audit](LIFE_OKX_IMPLEMENTATION_AUDIT.md) covers historical scope;
this document is the current delivery checklist.

## 1. Required behavior

Run the existing Hummingbot MM engine with three selectable reference profiles:

| Scenario | Reference behavior |
|---|---|
| `follow_market` | Follow the weighted percentage changes of selected pairs such as BTC-USDT and ETH-USDT |
| `sideways` | Quote around a saved LIFE anchor using configured spread and quote bounds |
| `follow_token` | Follow a configured token that the operator selects because it is trending |

The two follow scenarios share one calculation: `follow_market` accepts a weighted
basket; `follow_token` uses one pair with weight 1. YAML supplies default profiles;
the admin API can select additional verified reference pairs at runtime. These
modes control the bot's quotes, not the resulting market price.

Admin API selects `market_mode: spot | perpetual | both`, profile and duration.
`both` runs spot and perpetual concurrently with the **same scenario and clock**,
using separate market budgets/limits. Random mode selects a scenario/profile at
step boundaries until a finite overall deadline; it keeps the chosen market mode.

Use one service/container, the existing V2 runner, and one shared MM controller
implementation instantiated for each enabled connector (`okx`, `okx_perpetual`).
Reusing V2 covers both markets without adding a separate V1 implementation.

## 2. Existing code to extend

**Reuse first is a delivery requirement:** use existing configuration, then an
existing extension point or a small shared-helper extraction. Build new behavior
only for a demonstrated gap. An absent HTTP endpoint does not mean the underlying
trading, history or accounting logic needs to be implemented again.

The inventory distinguishes upstream Hummingbot foundations from infrastructure
already completed in this fork. Neither category is new LIFE work.

| Existing component | Origin | Reuse and remaining work |
|---|---|---|
| [V2 MM base](../../hummingbot/strategy_v2/controllers/market_making_controller_base.py) and [PMM simple](../../controllers/market_making/pmm_simple.py) | Upstream | Reuse quote levels, sizing, refresh and executor actions; supply the LIFE reference through `update_processed_data()` |
| [V2 runner](../../scripts/v2_with_controllers.py) and [executor orchestrator](../../hummingbot/strategy_v2/executors/executor_orchestrator.py) | Upstream, extended by the port | Load one or two instances; reuse lifecycle, order tracking, held positions, recovery and cached performance reports |
| [Market data provider](../../hummingbot/data_feed/market_data_provider.py) and existing controller config models | Upstream | Reuse prices, subscriptions, metadata and supported validation/settings; add only basket/anchor and session-specific fields |
| [OKX spot](../../hummingbot/connector/exchange/okx/okx_exchange.py) / [perpetual](../../hummingbot/connector/derivative/okx_perpetual/okx_perpetual_derivative.py) | Upstream, extended by the port | Reuse books, balances, orders, positions, funding and throttlers; fix required quantity/demo gaps in place |
| [Recorder](../../hummingbot/connector/markets_recorder.py), [ORM models](../../hummingbot/model) and [history commands](../../hummingbot/client/command/history_command.py) | Upstream, extended by the port | Reuse recorded orders/statuses/fills/funding and history queries; add bounded API filters/serialization and daily grouping |
| [Performance metrics](../../hummingbot/client/performance.py), V2 reports and [balance commands](../../hummingbot/client/command/balance_command.py) | Upstream, extended by the port | Reuse compatible fee, volume, PnL and account-data helpers; adapt market scope, freshness and Decimal output without a second calculation engine |
| [MQTT commands](../../hummingbot/remote_iface/mqtt.py) | Upstream | Start/stop/status/config/history RPC already exists; reuse applicable command semantics and underlying helpers for the HTTP adapter |
| [aiohttp service](../../hummingbot/cli/service.py) | Completed port | Add authenticated admin routes and independent session/deadline handling on the existing port/owner loop |
| [Control store](../../hummingbot/model/bot_state_store.py) and [send guard](../../hummingbot/core/order_submission_guard.py) | Completed port | Reuse versioned commands, owner fencing, HALT and transactions; extend with current-session fields and durable receipts |
| Existing YAML/env loaders, SQL runner and offline simulator | Upstream configuration plus completed port tooling | Reuse configuration, idempotent migrations, PostgreSQL connections and test fixtures; extend only for the missing behavior |

MQTT handlers depend on the interactive application; do not call them directly
from the service or introduce a broker for HTTP control. Route trading commands
through the existing PostgreSQL owner/control path. Extract a small shared helper
only when needed, preserving the original caller and its regression coverage.
Likewise, reuse compatible performance calculations without treating a partial
fill window as exact daily PnL or summing unlike spot/perpetual metrics.

The remaining additions are the weighted/fixed LIFE reference, finite/random
session coordination, HTTP Basic Auth/Swagger adapters, bounded daily aggregation
and reconciliation reports, and historical balance snapshots absent from current
storage. Fix the identified connector/recorder gaps in their existing code paths.
Keep current state and retry receipts in PostgreSQL using existing models/connections
and simple idempotent snake_case SQL. Keep configuration in YAML. No parallel trading
engine, order tracker, fee/PnL engine, command worker or persistence framework is
required; no session-history subsystem or order-to-session attribution is required.

## 3. YAML profiles

Package `deploy/cloudrun/conf/life_mm_profiles.yaml` as
`conf/life_mm_profiles.yaml`. Reuse existing controller configuration models for
spread, order size, refresh, inventory/exposure limits and perpetual settings.
The API selects deployed profile IDs and validated runtime fields, including
reference pairs/weights; it does not edit YAML or change the LIFE trading target.

Reuse the runner's controller loader and `ControllerBase.update_config()` for
supported updatable fields. Keep effective API session configuration authoritative
until an explicit switch; the runner's periodic YAML reload must not silently
overwrite it. Pair/market/session changes still follow the switch lifecycle.

Illustrative profile fragment, not yet runnable:

```yaml
reference_connector: okx
profiles:
  follow_market:
    scenario: follow_market
    references:
      - pair: BTC-USDT
        weight: "0.6"
      - pair: ETH-USDT
        weight: "0.4"
  sideways:
    scenario: sideways
    anchor: session_start_mid
  follow_token:
    scenario: follow_token
    references:
      - pair: ETH-USDT
        weight: "1"
random:
  profile_ids: [follow_market, sideways, follow_token]
  step_duration_seconds: 600
```

The complete YAML also supplies market/controller settings, permitted/default
runtime durations and per-market limits. Return these through `/profiles` so FE
uses the same validation rules. Credentials stay in `.env`/injected secrets.

Default profile IDs are `follow_market`, `sideways` and `follow_token`, matching
their scenario names. `follow_token` follows the selected reference; it does not
detect trends or automatically find a trending token. Reference pairs remain
configurable, including validated API overrides outside the YAML defaults.

`random` is a session selection policy, not a fourth profile or quote formula.
Use `selection: fixed` with one `profile_id`, or `selection: random` with optional
`profile_ids` and `step_duration_seconds`. Without `profile_ids`, random uses
`random.profile_ids` from YAML; an API override may narrow that pool. The UI can offer **Random** alongside the three fixed
choices while submitting the corresponding selection policy to the API.

Keep these two controls separate in the UI/API:

- **Selection:** one specific profile (`selection: fixed`, `profile_id`) or a
  random profile pool (`selection: random`, optional `profile_ids`).
- **Market:** `market_mode: spot | perpetual | both`, applicable to either selection.

For example, fixed selection sends `{"selection":"fixed","profile_id":"follow_market"}`;
random selection sends `{"selection":"random","profile_ids":["follow_market","sideways","follow_token"],"step_duration_seconds":600}`.
These are selection fragments of a start/switch command; both commands also supply
the market mode, duration and expected control version. Omit `profile_id` for random
and omit random-only fields for fixed. Random may draw the same profile on consecutive
steps; avoiding repeats would be a separate selection rule.

Follow calculation:
`reference(t) = LIFE_anchor × (1 + Σ weight_i × (source_price_i(t) / source_anchor_i − 1))`.
Use Decimal; require non-empty unique supported pairs, positive finite weights and
an exact total weight of 1. All references in this MVP are USDT-quoted pairs.
`follow_token` requires one pair with weight 1. No silent weight normalization.

YAML supplies default baskets, not a closed pair allowlist. A fixed start/switch
command may override `references`: several pairs for `follow_market`, exactly one
for `follow_token`. The source is fixed by `reference_connector` (initially OKX
spot), shared by both trading markets and routed through the chosen demo/live
environment. Read each reference's bid/ask midpoint from that connector's order
book through the existing `MarketDataProvider`; both trading markets use this
same spot benchmark source. API requests cannot supply arbitrary connectors or
feed URLs. DEX feeds and perpetual-only reference pairs are outside the initial
source support.

A pair absent from YAML is accepted if it exists and is tradable on that source,
has the supported USDT quote currency and usable fresh data, and fits the configured
maximum basket size. Reuse existing instrument discovery/subscriptions; validate
and initialize every required reference before quoting. Persist effective pairs,
weights and anchors in current session state so restart recreates the same feeds
without a YAML edit or deployment. Changing pairs/weights requires a switch.

Use `MarketDataProvider.initialize_order_book(s)`, `remove_order_book(s)` and
`get_price_by_type()` for reference feeds, preserving subscriptions still needed
by another controller or the trading target. Discover supported instruments from
connector metadata; `get_trading_pairs()` only lists configured/subscribed pairs.

For example, an operator can select a newly trending AVAX-subnet token's verified
OKX USDT pair through `follow_token` without adding a YAML profile. If that token
is only on a DEX or has no supported feed, return `reference_pair_unavailable`;
adding that data source is separate work. Sharing an ecosystem does not create
a price feed or automatically select a trend.

Random steps use the configured profiles' baskets. If any required source is stale
or missing, block/cancel the basket's quotes instead of dropping that source and
renormalizing. Restart preserves the saved basket/anchors within the current step.

Save each enabled market's LIFE anchor as well. Sideways uses its saved LIFE
anchor. Reuse the existing market-data provider for source prices. Absolute
BTC/ETH prices must never become LIFE quotes.

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

Only three trading commands are needed: **start, switch, stop**. Start/switch specify the
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
- Random chooses uniformly among the selected profile IDs, defaulting to the YAML
  `random.profile_ids` pool. Use fixed step windows anchored to session start and capped by
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
Swagger UI/schema access require Basic Auth. Keep existing minimal health probes separate.

Proposed env settings: `ADMIN_API_ENABLED=false`, `ADMIN_API_USERNAME`,
`ADMIN_API_PASSWORD` (no usable credential defaults), `ADMIN_CORS_ORIGINS=*`
by default. Reuse dotenv/Cloud Run secrets and centralized defaults; no `LIFE_`
prefix. Enabling the API requires credentials. Use constant-time checks, HTTPS in
deployment, `401`/`WWW-Authenticate` for bad credentials and redact auth from logs.
FE sends operator-supplied credentials per request and keeps them out of its build.
Support `*` for any FE origin, or a comma-separated explicit origin list. With
`*`, return `Access-Control-Allow-Origin: *` without `Allow-Credentials`; FE sends
its Basic `Authorization` header explicitly with `credentials: "omit"`. Allow the
configured request headers; actual requests remain authenticated after preflight.
FE can poll; no new login/token service is needed.

Paths below are relative to `/api/v1/admin`:

| Method / path | Purpose |
|---|---|
| `GET /profiles` | Available scenarios, reference source, default baskets/weight rules, market modes, validated settings, defaults and limits |
| `GET /reference-pairs` | Search/paginate supported source instruments for FE selection, including pairs absent from YAML; reuse instrument metadata and expose its freshness |
| `GET /status` | Requested/actual state, per-market readiness, current session/random step, references, expiry/time left, versions, staleness and HALT reason |
| `POST /commands` | Start/switch/stop with validated market mode, selection and duration |
| `GET /commands/{id}` | Accepted/applying/succeeded/failed/superseded result and effective version/session |
| `GET /orders` | Existing bot-owned open/history orders, filtered by market/pair/time |
| `GET /orders/{id}` | Local/exchange order identity, current status, bounded status timeline and links to its fills |
| `GET /fills` | Existing fills/fees filtered by market/pair/time |
| `GET /balances` | Canonical account LIFE/USDT cash, available balances and equity with explicit field meanings and snapshot freshness |
| `GET /positions` | Actual perpetual positions, leverage/margin data available from the connector, and freshness; visible after quoting stops |
| `GET /performance` | Separate spot/perpetual PnL, fees, volume and applicable funding data, labeled with valuation basis/time |
| `GET /statistics/daily` | Separate spot/perpetual daily series for fills, buy/sell volume, fees/rebates and applicable funding; account balance points are returned once with account scope |
| `POST /reconciliation/check` | Request one bounded, idempotent observational check against OKX; return `202` and a receipt/check ID |
| `GET /reconciliation` | Latest check state/report with scope, coverage, timestamps and differences; FE polling reads stored results |
| `GET /openapi.json` | Generated OpenAPI contract with FE examples |
| `GET /docs` | Swagger UI using the generated schema, with Basic Auth and interactive API requests |

Swagger annotations belong on **API handlers**, using decorator/docstring metadata
for summary, tags, operation ID, parameters/headers, request body, responses/errors
and Basic Auth. Reference the existing request/response models for data schemas;
keep business logic and storage code independent of documentation metadata.

During P3, add `.PHONY: swagger` and `make swagger` to `Makefile`. The target
generates and validates `docs/api/openapi.json` from the registered routes, handler
annotations and referenced models, without starting the bot, connecting to a
database/exchange or reading secrets. Repeated generation must be deterministic;
CI verifies that annotations/models and the generated contract agree.

Serve the same generated contract at `/openapi.json` and load it from `/docs`.
Package the schema and Swagger UI static assets with the service so documentation
works locally without a CDN. Swagger UI supports Basic Auth and the
`Idempotency-Key` header for trying commands, with credential persistence disabled.

Reuse ORM/snapshots, `PerformanceMetrics` and the V2 orchestrator's cached reports
where their definitions match the response. Reuse underlying history/balance
helpers instead of CLI presentation code or MQTT callbacks; do not shell out to
CLI or add per-scenario accounting. Monetary values are Decimal strings, timestamps
UTC and history reads have bounded pagination. Mark unavailable/stale data explicitly.

### Daily statistics and balance charts

Use existing `orders`, `order_statuses`, `trade_fills`, `funding_payments` and
connector account data. `/fills` also accepts an order ID for the detail screen.
Initial UI scope is current LIFE/USDT balances, daily fees/volume/fill counts and
daily observed balance charts. `/statistics/daily` returns one bucket per day/market,
including `observed_at`, coverage and data-source fields; use a default seven-day
range and a maximum 90-day range. Market filters apply to trading metrics;
account balance points have an explicit account scope and are never summed across
market series.

- Reporting endpoints use `market=spot|perpetual|all`, mapping the existing
  `okx`/`okx_perpetual` records to explicit market labels. The default `all` returns
  separate series, not a merged trading total. Rows retain market identity even
  when both connectors use the same `LIFE-USDT` pair name. Filters are independent
  of the currently running mode, so switching to spot does not hide perpetual history.
- FE presents two views: **Spot** shows its orders/fills, buy/sell volume, trading
  fees/rebates and available spot performance; **Perpetual** shows its own trading
  metrics plus positions, margin/leverage and observed funding. Funding is
  `not_applicable` for spot. Daily position/margin history is not implied by the
  existing balance-snapshot feature; those fields remain current observations.
- Example reads: `/statistics/daily?market=spot` and
  `/statistics/daily?market=perpetual`. With `market=all`, return distinct `spot`
  and `perpetual` series plus one account-scoped balance series. Swagger documents
  these response scopes so FE never combines them accidentally.
- Aggregate actual recorded fee amounts by original currency: charges, rebates
  and net fees. Show USDT totals only for valid recorded conversions, with
  unconverted amounts/coverage visible. Funding paid/received is separate and
  labeled as observed when settlement history is incomplete.
- **Required narrow fix:** the recorder currently writes zero when quote-fee
  conversion fails. Preserve unavailable conversions for new fills and flag
  ambiguous legacy values; a failed conversion must not look like a free trade.
  PostgreSQL order/fill/position decimals now use unscaled NUMERIC following the
  demonstrated BTC precision fix below. Legacy floating-point funding precision
  is unchanged in the delivered runtime; P7 below plans exact bill-based funding
  ingestion and restart backfill. Decimal-string output does not recover
  precision already lost.
- Reuse fetched account fields for LIFE/USDT `cashBal`, `availBal`, `eq`, `availEq`
  where present, labeling unavailable fields. Both connectors can expose the same
  account balance; select one canonical account view and never add their USDT
  balances together. Equity must not be labeled wallet cash. Perpetual-only runs
  still need the account snapshot for LIFE, not only the connector's USDT/USDC view.
  Both UI views may reference that shared account summary, clearly labeled. Show
  configured spot/perpetual budget allocations separately from actual exchange
  balances; do not invent per-market wallet balances by splitting shared collateral.
- Add one small `daily_balance_snapshots` table through the existing SQL runner.
  The current owner upserts the latest fresh observation at most once per minute
  per account/environment/currency/reporting timezone/day, using the existing loop and
  database connection. Record actual observation time and source semantics;
  a repeated observation after restart is idempotent. No separate scheduler is needed.
- Use one configured reporting timezone, initially `Asia/Ho_Chi_Minh`, with UTC
  timestamps in storage and explicit half-open day boundaries. Store the bucket's
  timezone; do not relabel old snapshots under a new timezone. Late fills update
  their event day, and duplicate fills are counted once.
  Every PostgreSQL connection explicitly sets `timezone=UTC`, regardless of the
  database/server/host default. Timestamp columns use `TIMESTAMP WITH TIME ZONE`;
  recorder epoch timestamps are UTC instants and API timestamps serialize with
  `Z`. Local reporting dates are bucket labels, not local-time timestamps.
- Historical balances start with these snapshots. Missing days stay missing;
  the latest sample in a day is labeled "last observed", not an exact midnight
  balance. Do not reconstruct balances from fills alone or report exact daily
  PnL from isolated daily fills without opening inventory/position valuation.

### Bounded reconciliation checks

Compare bot-owned order status/cumulative fills, fill identity/quantity/fees and
perpetual long/short exposure using existing connector reads and local records.
Return checked scope/counts, local/exchange observation times, coverage and linked
order IDs. Select `spot`, `perpetual` or `all`; an all-market check reports separate
spot/perpetual findings and coverage. A successful spot check cannot hide an
unavailable or mismatched perpetual check. Result states are `not_checked`, `checking`, `matched`, `mismatch`,
`incomplete`, `unavailable`; `matched` applies only to a complete, consistent checked
scope. A concurrent fill, missing page, stale source or timeout cannot be reported
as a match. Account-wide cashflow reconciliation is outside this check.

Run at most one bounded check at a time in the existing owner service, reusing
connector throttlers and the planned receipt mechanism. Bound time range, pages
and execution time; repeated FE polling reads the latest stored report without
calling OKX. A check must not bump the trading control version or reset a session:
the current service restarts its core when that version changes.
If the owner or authenticated read client is unavailable, report `unavailable`;
checking must not start/restart a strategy to obtain connector state.

The existing `BotOwnership.reconcile()` is corrective startup recovery: it updates
trackers/persistence, can cancel orders and changes send permissions. Extract/reuse
its read/comparison pieces where suitable; do not call that lifecycle method from
an observational endpoint. Live position comparison must include active as well as
held exposure. This check writes its report only; it does not repair fills, cancel
orders, alter balances or clear HALT. Existing risk/recovery enforcement remains
active, and operator recovery handles confirmed discrepancies separately.

Example command body, with Basic Auth and `Idempotency-Key` headers:

```json
{
  "action": "start",
  "expected_version": 12,
  "market_mode": "both",
  "selection": "fixed",
  "profile_id": "follow_market",
  "references": [
    {"pair": "BTC-USDT", "weight": "0.6"},
    {"pair": "ETH-USDT", "weight": "0.4"}
  ],
  "duration_seconds": 3600
}
```

Random requests use configured profile IDs, overall `duration_seconds`
and `step_duration_seconds`. Switch/stop identify the current session. Start
requires no active session. Reject arbitrary paths, unknown profiles and unsupported
market/account settings.

For trading commands, reuse control-version CAS and a small durable idempotency receipt in the same
transaction. Check matching retries before CAS. A matching retry returns its
original command without resetting time;
a reused key with different data or stale version returns `409`. Return `202` on
acceptance, then let the existing owner apply the request. Completion requires the
actual requested state: the existing acknowledged version alone is not success.
Publish validation (`422`), not-found (`404`) and unavailable (`503`) errors in the
API schema. No second command worker/service is required.

## 7. TDD and delivery order

**TDD/unit → PostgreSQL integration → local image E2E → local load/soak → OKX Demo.**
All P1–P5 execution stays local with dummy credentials and isolated exchange
fixtures. OKX Demo account access is reserved for P6 after the local gates pass.

At the start of each phase, record a short reuse map in its implementation evidence:
the existing source/helper/test, the unmet requirement, and the smallest necessary
configuration/extension/fix. If existing behavior already meets the requirement,
reuse it and run its relevant tests; do not create a replacement implementation.
This is part of the phase, not a separate framework or prerequisite project.

For each behavior/fix, follow **Red → Green → Refactor**:

1. Write the smallest behavioral/contract test and run it failing for the intended
   missing behavior, rather than an import/environment error.
2. Implement the minimum change using the existing components; make that test pass.
3. Refactor with focused regression tests green. Record Red/Green commands/results
   and commit the completed phase with tests and code together.

Use fake clocks, fixed random seeds and Decimal for deterministic unit tests;
real disposable PostgreSQL for transactions/CAS/recovery; the independent simulator
for actual connector/API/image flows. Do not rewrite existing passing behavior to
manufacture a Red result. Define load assertions first and measure a baseline;
optimize only demonstrated bottlenecks if the existing code misses the targets.

### P1 — Reuse V2 for both markets and three profiles

- [x] **Red:** cover weighted returns, single-token equivalence, anchor calculation, a supported pair absent from YAML, unknown/duplicate/non-USDT pairs, basket-size limits, invalid weights/totals and a stale basket member. Reproduce fractional/zero-entry-price perpetual quantity errors before fixing them.
- [x] Add the YAML catalogue through existing config models/loaders; validate market modes, durations, per-market limits and API basket overrides against the source instrument metadata.
- [x] Reuse dynamic order-book subscriptions and controller configuration lifecycle; test shared-feed retention and prove periodic YAML reload cannot overwrite the effective API session selection.
- [x] Extend one V2 MM controller with shared weighted-follow/fixed-anchor logic; reuse quote/executor lifecycle for spot and perpetual.
- [x] Fix required perpetual quantity handling; configure maker execution, position holding, leverage and supported CROSS/HEDGE settings explicitly.
- [x] **Green/refactor:** verify listing/freshness, both-market budgets, held/in-flight exposure and existing V2 regressions; record evidence and commit.

P1 evidence: [implementation progress](../implementation/LIFE_OKX_PROGRESS.md).
510 targeted new/regression tests pass locally. Empty listing books require an
explicit initial anchor and a fresh snapshot; partial fills survive quote refresh.
The catalogue validates both-market selection/budgets; actual session admission,
shared readiness, restart/expiry and final-send integration are delivered in P2, followed
by image E2E, load and Demo gates. P1 does not establish a running admin-controlled
LIFE service or price-path guarantees.

### P2 — Current session and random selection

- [x] **Red:** fake-clock expiry, step/profile revocation, market-mode switches, cancel/fill races, random draws, persisted basket/anchor/feed recovery, restart/downtime and both-market readiness failures.
- [x] Persist current-session/basket/anchor state using existing PostgreSQL/migration/control machinery; implement start/switch/stop and independent expiry/final-send checks.
- [x] Persist random draws/windows shared by both markets; preserve selected weights, anchors and deadlines across restart.
- [x] **Green/refactor:** real-PG CAS/replay/recovery tests and existing ownership/HALT regressions pass; record evidence and commit.

P2 evidence: [implementation progress](../implementation/LIFE_OKX_PROGRESS.md#p2--current-session-and-random-selection).
1,178 unit/regression tests and all 82 disposable-PostgreSQL tests pass. Session
control is available as internal service/store behavior; its authenticated HTTP
commands, durable retry receipts and FE completion states are P3, not yet exposed.
The actual image/simulator matrix and Demo transport/account acceptance remain P4–P6.

### P3 — Admin API for FE

- [x] **Red:** actual HTTP tests for Basic Auth, malformed baskets/modes, retries, stale versions, conflicting commands, completion/failure states and Decimal/freshness output. Cover protected Swagger UI/schema and missing or inconsistent handler documentation.
- [x] Add routes/auth/env validation to existing aiohttp; reuse owner/control flow with transactional idempotency receipts and preserve health probes/CORS rules.
- [x] Reuse history queries, account data, `PerformanceMetrics` and cached V2 reports where applicable; extract only helpers required by both callers, preserving existing CLI/MQTT behavior and avoiding duplicate fee/PnL or order-sync implementations.
- [x] Expose profiles/reference-pair discovery/status and existing order/fill/balance/position/performance data; publish schema/examples including weighted basket selection and a supported pair absent from YAML.
- [x] **Red → Green:** daily fee/rebate/funding grouping, unavailable conversions, late/duplicate fills and timezone boundaries. Add daily statistics and the minimal balance-snapshot migration/upsert; verify shared-account deduplication, cash/equity labels and missing-day/restart behavior with real PostgreSQL.
- [x] **Red → Green:** separate spot/perpetual history, daily series, performance and reconciliation coverage, including identical pair names, mode switches and one market unavailable. Prove shared balances appear once, configured budgets are not reported as exchange balances, and spot funding is not applicable.
- [x] **Red → Green:** order details and bounded reconciliation reports for missing/mismatched data, pagination gaps, concurrent fills and read failures. Prove a check cannot mutate exchange orders, repair ledger rows, restart the strategy or clear HALT; keep command/check completion distinct from report coverage.
- [x] Annotate API handlers and reuse request/response model schemas; add deterministic `make swagger` generation/validation and CI drift checks for `docs/api/openapi.json`.
- [x] Package Swagger UI at `/api/v1/admin/docs`, reading the generated `/api/v1/admin/openapi.json`; verify Basic Auth, command headers and schema/UI availability without external assets.
- [x] **Green/refactor:** exercise all three market modes and supported commands with bounded read/query assertions; record evidence and commit.

P3 evidence: [implementation progress](../implementation/LIFE_OKX_PROGRESS.md#p3--admin-api-reporting-and-observational-reconciliation) and [admin API guide](../admin-api.md).
1,362 unit/regression tests, 2 subtests and all 100 disposable-PostgreSQL tests pass.
Swagger drift and changed-file pre-commit checks pass. Deployment-image integration,
local load/soak and exchange Demo acceptance remain P4–P6.

### P4 — Local image E2E and offline Demo transport contracts

- [x] **Red:** instantiate real connector/auth/data-source paths against local transports; verify selected demo/live mode, final REST headers and public/private WS routing, including reconnects. Existing URL-helper tests are insufficient.
- [x] Wire an explicit process setting such as `OKX_TRADING_ENV=demo|live` into both existing connectors. Keep it separate from API `market_mode`; no API hot switch between demo/live. Demo runs require dedicated demo credentials and never fall back to live keys/routes.
- [x] Extend existing routing/auth rather than create demo connector forks. Cover instrument/account/leverage/order/cancel/reconciliation requests and reference feeds; both enabled markets use the same environment.
- [x] Update affected OKX WebSocket URLs to port 443/default `wss://`, including relevant candle feeds; verify regional endpoints against the account site and reject unsupported combinations.
- [x] Extend the simulator with LIFE spot/SWAP plus reference pairs. Verify three scenarios × spot/perpetual/both, weighted baskets, random selection, API switches, expiry and restart through the deployment image.
- [x] **Green/refactor:** run relevant existing regression, real-PG and image E2E gates, including FE history/daily-statistics/reconciliation workflow, stale feed/DB faults and held exposure. Verify daily totals against simulator fills and observed balances without double-counting `both`. Save image/config hashes and commit. No OKX account is used here.

P4 evidence: [implementation progress](../implementation/LIFE_OKX_PROGRESS.md#p4--local-image-and-demo-transport-integration) and [routing guide](../okx-environments.md).
All six deployment-image scenarios pass, including the nine LIFE profile/market
combinations and upgrade/rollback. 1,557 regressions, 2 subtests and all 101
disposable-PostgreSQL tests pass. Real OKX account checks remain P6.

### P5 — Final local load and soak gate

Reuse the image, disposable PostgreSQL and simulator; add one opt-in Python/aiohttp
load runner/Make target rather than another service or load-testing framework.
The bot runs at **1 CPU / 2 GiB**; PG/simulator/load generation have separate
recorded limits. Record host architecture/emulation, image/config hashes, fixture
sizes and requested **and achieved** rates. Reuse existing large-history/query
fixtures from [the PostgreSQL performance review](../postgres-performance-review.md).
Seed sufficient virtual inventory/collateral for the valid-fill workload while
retaining all configured risk caps.

These are initial local test budgets, **not measured capacity**:

| Profile | Workload |
|---|---|
| Mixed load | 5-minute warmup, then 15 minutes; 10 FE clients, 20 aggregate reads/s including daily statistics/history/report polling, 100 aggregate market updates/s and 1 valid fill/s, with a multi-pair basket and both markets |
| Bursts/contention | Short 5× market-data bursts, duplicate/out-of-order private events, concurrent command retries and stale-version conflicts |
| Soak | 60 minutes in `both`, finite random steps, quote refreshes and bounded history reads |
| Faults under load | Separate feed interruption, delayed/failed DB, owner restart and expiry with queued events; report separately from healthy latency/errors |

`make test-mm-load` runs this opt-in local gate. `MM_LOAD_PROFILE=probe` is a
60-second diagnostic, not the final gate; `all` runs the complete durations.
The fixed mixed window enforces the 100-update/s and 1-fill/s budgets. Soak
records achieved rates across intentional random-step admission gaps. Its first
and last five minute samples must show median RSS growth ≤64 MiB, median book
backlog ≤max(10, initial+4), and median pool p95 ≤max(20ms, initial+5ms).
Market-rate counts use the simulator's two monotonic observation times, allowing
one four-book batch at the measurement boundary. Thresholds are unchanged after
measurement; expected conflicts and injected-fault failures are reported separately.

- [x] Define assertions before measuring. Record p50/p95/p99 API latency, command application time, event-loop lag/event age/backlog, recorder latency, CPU/peak RSS, DB pool waits/connections, query counts and errors.
- [x] Healthy aggregate reads/command acceptance (per-route timing reported separately): p95 ≤500ms, p99 ≤1s, no unexpected timeouts/5xx; the generator achieves its sustained offered rate. Measure actual application separately: healthy local stop/cancel ≤5s and start/switch ≤15s, not merely `202` or acknowledgement.
- [x] Burst backlog drains within 10s of returning to steady load; no sustained queue/pool/memory growth during soak; peak bot RSS ≤1.5 GiB. Save per-minute samples so a late leak is visible. Report unmet budgets and the bottleneck rather than silently lowering load or relaxing thresholds.
- [x] Reuse N+1/query-count regressions as page/history size increases; cached performance reads retain the existing cache behavior. Add cache/index changes only when these measurements justify them.
- [x] Include daily-snapshot writes and one rate-bounded reconciliation check during the local load profile; report polling must not multiply exchange calls or block quote/deadline handling. Date-range aggregation must remain bounded on the large-history fixtures.
- [x] Independent exchange ledger and PostgreSQL agree on unique fills/fees/positions. Retries never duplicate commands/orders or reset deadlines. No new sends are authorized after step/session revocation, expiry or HALT; already in-flight effects must reconcile.
- [x] Count expected conflict/fault responses separately. Fix demonstrated failures using regression tests, rerun the affected load profile, save sanitized JSON/report evidence and commit before P6.

Accepted locally on 2026-10-25 with the standard deployment image, the complete
six-scenario E2E matrix, 107 PostgreSQL tests and all regression/Swagger checks.
Mixed (15 minutes): aggregate p95 **73ms**, p99 **545ms**. Soak (60 real minutes):
p95 **74ms**, p99 **584ms**, 72,000 completed reads and 3,600 valid fills. All five
fault windows pass; independent recorder/exchange comparison agrees on 4,890
unique fills. Peak bot RSS **341 MiB**; burst backlog drains in **0.53s**.

Evidence and per-route caveats: [load report](../life-mm-load-tests.md),
[sanitized acceptance data](../life-mm-load-acceptance.json). Daily statistics
cache misses remain approximately 1.1 seconds, so aggregate acceptance is not a
per-endpoint latency guarantee. Upstream teardown pending-task diagnostics remain
visible; no healthy HTTP failures/HALT or sustained resource growth was observed.
These caveats describe the original immutable P5 artifact; the follow-up below
records subsequent source changes separately.

### P5 follow-up — Measured cache, SQL and lifecycle optimization

- [x] Let valid daily-cache hits bypass an unrelated aggregate miss; preserve coalescing, TTL, filter isolation and error behavior with a failing-then-passing test.
- [x] Measure daily reports on 100,000 fills. Reject slower combined-query trials; disable JIT only inside the reporting transaction, retain exact fees/volumes and verify connection setting restoration.
- [x] Replace per-currency snapshot SQL with one batch; retain ownership fencing, DB-clock freshness, UTC instants, update cadence and nullable/exact values. Verify duplicate/stale/future observations.
- [x] Retain and drain controller generations, including blocked/failed startup and immediate stop/start/stop. Await reference release and public-network closure; retire the action listener before waiting for slow cleanup.
- [x] Trace residual pending-task diagnostics in Docker, reproduce the orphaned perpetual funding-payment poller and cancel/drain it before replacing its notifier on network stop. Confirm clean final-image debug logs.
- [x] Add native lifecycle regressions to the Makefile regression target and run the full 111-test PostgreSQL suite. Record idle before/after measurements.
- [x] Validate the final rebuilt image with affected offline E2E and burst load; record evidence and complete the separate [optimization report](../life-mm-optimization-review.md). Keep the original 60-minute soak evidence attached to its original image.

Follow-up complete: native daily-report median **812 → 504ms**; 100-currency
snapshot **100 → 1 SQL**, median **184 → 28ms**. All **1,249 native regression**
and **111 PostgreSQL** tests pass. Final-image LIFE E2E passes all nine
market/profile combinations and recovery, with zero pending-task/unawaited
coroutine diagnostics. The standalone 90-second burst passes all budgets and
independent ledger checks (90 fills); aggregate read p95/p99 **64/518ms**.
Daily-route p95 remains **570ms** in this short window; the prior 60-minute soak
is not relabelled as validation of this new image. Details, hashes and limits
are in the separate report.

### P6 — Final acceptance against OKX Demo

**Demo protocol, natural fills/fees and filled-exposure restart passed; funding settlement remains open.** P1–P5
local gates have passed. Dedicated Demo setup uses isolated UTC PostgreSQL,
Futures/Hedge/Cross 1x, explicit BTC-USDT trading and AVAX-USDT single-token
following. Two real defects were fixed with TDD: reusing a target book as a
reference (`e9ab23687`), and filtering unusable preopen contract metadata
(`c9b858e36`). The affected suites pass 140 tests and 201 tests/seven subtests
respectively; these suites overlap and are not a unique-test total.

The observation image passes selected LIFE offline E2E/recovery and all nine
real Demo profile/market cases. Its 30-minute random/both session completed with
three steps, preserved anchors/deadline across restart, two matched spot/perpetual
reconciliations and automatic expiry/cancellation. That image created 288
orders; all are cancelled. At that protocol's completion the DB had 300 including the earlier 12-order
pilot, zero fills, UTC, no HALT and no owner. Those exchange reads showed no pending
orders or perpetual positions and unchanged cash. No new full load/soak or Cloud
Run deployment was performed. See [Demo acceptance](../okx-demo-acceptance.md),
[portable evidence](../okx-demo-acceptance.json) and
[environment setup](../okx-environments.md).

Post-observation log review identified duplicate cancel response `51400` being
misclassified as an error and a late-acknowledgement terminal-state race. The
mapping and shared terminal guard now pass 1,259 regression tests/nine subtests;
commit `32285f5c8` and standard image `17d530e26018...` pass rebuilt-image E2E,
a direct Demo `51400`/terminal-readback probe under real PG ownership, and idle
service startup/clean shutdown. The probe submits no new orders. The recorded
30-minute run retains its original `8401c10c2c5c...` image provenance; no new full
load/soak or 30-minute Demo run is claimed for the cancellation fix.

The unchanged retained image subsequently recorded five natural maker fills
(three spot/two perpetual). All original fee amounts and currencies match Demo
trade reads. A real restart preserved a 0.0002 BTC SHORT, anchors and deadline;
both-market reconciliation matched. Final exposure is SHORT 0.0004 BTC with no
pending orders, 320 persisted orders/five fills, released ownership and no HALT.
Legacy scale-six spot quantity truncation is explicitly reported, not described
as lossless reconciliation. A capped no-new-order funding smoke passed. A bounded
local watcher will observe the 2026-10-26 00:00 UTC settlement; payment stays
unverified until a real bill matches the recorder. See
[natural-fill evidence](../okx-demo-fill-acceptance.json) and the
[updated acceptance report](../okx-demo-acceptance.md).

OKX documents Demo API keys, the `x-simulated-trading: 1` REST header and separate
demo WebSocket endpoints; REST may share the production host. Verify the account's
site and supported demo instruments rather than relying on hostname alone.
[OKX Demo Trading Services](https://www.okx.com/docs-v5/en/#overview-demo-trading-services).

P4 closed the originally identified Demo gaps: spot selection, simulated REST
headers for both auth paths, and the perpetual public-book WS domain. Real local
HTTP/WS transport tests and the deployment-image matrix verify that routing;
they do not replace dedicated-account acceptance here.
OKX also announces public-internet WebSocket port 8443 retirement on **2026-10-31**;
port 443 is already available. Checked 2026-10-25.
[OKX API changelog](https://www.okx.com/docs-v5/log_en/#upcoming-changes-websocket-port-8443-discontinuation).

- [x] Require passing P1–P5 evidence, the tested image and a dedicated demo account/API key with Read/Trade access supplied through the existing secret/env mechanism. Keep demo database/state separate from any live bot. Default local/CI tests must never contact OKX.
- [x] Check demo instruments/account settings first. If LIFE is unavailable, explicitly configure supported substitute spot/SWAP instruments (for example BTC/ETH only if available); retain LIFE/listing-specific acceptance in the local simulator. Do not silently rename LIFE in a trading request.
- [x] From local, run bounded spot, perpetual and both workflows using demo balances: auth, books, post-only quotes, cancel, observed fills, positions, Basic Auth commands, weighted references, random steps, expiry and restart. Compare history/statistics and the reconciliation report with Demo REST/private WS, including account balance labels. Record an unobserved fill/funding case as unverified, not passed.
- [x] Run a short 30-minute demo observation with conservative configured order frequency and existing exchange throttlers. Cancel test-owned quotes on completion and report retained positions as the stop policy requires. The stress/load driver remains local; do not stress-test OKX.
- [x] Record sanitized results, selected demo instruments, account mode, image/config hashes and remaining gaps; update this plan and commit. Demo checks exchange integration, not LIFE listing availability, production fill quality or profitability. No Cloud Run staging is required.

- [x] Finish rebuilt-image E2E and the direct known-terminal Demo cancellation probe for the post-observation fix; retain the original observation's image identity.

Focused follow-up checklist:

- [x] Observe natural fills and verify exchange/private-stream/recorder fees and recovery with filled exposure. Five natural maker fills, exact original fees/currencies, real SHORT recovery and matched reconciliation are recorded in the focused follow-up.
- [ ] Observe a Demo funding payment and verify its original-currency accounting against the exchange. No payment was observed in the bounded run.

### Post-Demo correctness — preserve recorded decimal quantities

The owner authorized this fix before deployment after real Demo spot fills lost
0.00000016–0.00000018 BTC each. The former tolerance masked these differences;
replaying a truncated fill could also be rejected as conflicting economic data.

- [x] Reproduce precision loss, duplicate-fill replay failure and sub-micro reconciliation false matches with failing tests.
- [x] Reuse the existing decimal adapter with unscaled PostgreSQL NUMERIC; preserve upstream SQLite storage and reject non-finite values.
- [x] Add replayable SQL converting all 15 scaled BIGINT fields once; test legacy rows, new precise values, replay and disabled-migration validation.
- [x] Sum natural units in daily statistics and compare quantities/prices exactly in observational reconciliation.
- [x] Test 8-decimal partial fills/fees/replay and round-trip every affected ORM field beyond 18 decimal places.
- [x] Finish full PostgreSQL/regression verification and deployment-image offline precision/recovery checks; record evidence and commit. 1,271 regression tests/nine subtests, 113 PostgreSQL tests and three extended precision checks pass. The same rebuilt image passes schema/spot, V2 spot/perpetual and all nine LIFE cases across combined/selected runs, plus a 60-second/100,000-history-fill diagnostic load probe. See [portable precision evidence](../postgres-decimal-precision.json).

Original Demo evidence remains historical: no old quantity is fabricated or
silently repaired, and the existing funding watcher continues on its original
image/schema. Existing funding/executor summary FLOAT fields are outside this
targeted scaled-decimal fix. See [runtime details](../postgres-runtime.md#decimal-storage).

### P7 — Durable funding observation and restart backfill (TDD; planned)

**Status: local implementation delivered; image E2E/load and final Demo acceptance in progress.** The owner requested this follow-up before deployment.
Reuse the existing service, OKX authenticated REST readers/throttlers, recorder,
PostgreSQL ownership, SQL runner and reporting handlers. No separate watcher
container, Cloud Scheduler, queue, Redis or general accounting framework is needed.
The local 07:00 Demo watcher is a bounded acceptance helper, not this feature.

Current gaps: the upstream perpetual connector polls payments every 120 seconds
only while its trading network is running. Startup initializes the last-payment
timestamp without emitting that payment, and the reader selects only the latest
matching bill. Stopping the strategy also stops its connectors. Consequently,
retained positions and payments during downtime can be absent from the recorder.
The existing `(timestamp, market, symbol)` identity also cannot distinguish every
exchange bill or isolate accounts/environments.

#### P7.1 — Red tests and minimal ingestion contract

- [x] Add failing tests for a running session, admin stop, session expiry, switching to spot-only with retained perpetual exposure, and restart after several settlements. Funding observation must never start a strategy, place/cancel orders, change leverage/mode, clear HALT or reopen the trading send gate.
- [x] Cover catch-up when the position was closed during downtime, no current position remains, or the final settlement bill arrives late. Startup backfill must not be gated solely on current nonzero positions.
- [x] Cover repeated/overlapping pages, late bills, two distinct bills sharing a timestamp, account/demo/live isolation, owner loss and commit failure. Verify exactly-once economic totals, not just successful polling.

#### P7.2 — Reuse OKX bill reads and PostgreSQL persistence

- [x] Extend the existing authenticated bills reader with bounded pagination/time windows and funding-bill filtering. Preserve actual `billId`, account/environment, instrument, UTC epoch-millisecond event time, signed amount and original currency; exclude transfers, trading fees and unrelated instruments. A historical settlement rate is nullable when unavailable; never label the current market rate as an old bill's rate.
- [x] Preserve the upstream `funding_payments` model/file format and add the narrow PostgreSQL `funding_bills` companion through `014_create_funding_bills.sql`. Its exact NUMERIC amounts/nullable rates and account/environment/bill-ID primary key preserve distinct same-time bills without a SQLite column/key migration. Identified bills are authoritative for overlapping event-time/instrument groups in daily reports; original legacy rows remain intact and explicitly unverified. Never invent legacy bill IDs or recover precision from FLOAT.
- [x] Use one authoritative PostgreSQL bill-ingestion path for both running and idle observation. Reuse the recorder's transaction/deduplication logic where compatible, but do not simultaneously count the upstream last-payment event and its bill as separate payments. Persist the bounded backfill checkpoint in existing bot state, scoped by account/environment, in the same owner-fenced transaction as the committed page. A failed write must not advance it.

#### P7.3 — Same-service read-only lifecycle

- [x] Schedule bounded account-scoped polls in the existing owner/service loop, initially reusing the 120-second cadence and current request timeouts. Retain only the authenticated REST reader needed after strategy connectors stop; do not bootstrap order books, executors or a trading session just to observe funding.
- [x] Continue observation for configured/previously traded perpetual instruments while exposure remains, including stopped/expired and spot-only sessions. Perform bounded startup catch-up regardless of current exposure and a final overlapping sweep after closure for delayed bills. If the whole service is down, collect missed settlements on restart; no local process can poll while its container is absent.
- [x] Reuse ownership fencing, existing connection/session cleanup, throttling and retry patterns. Standby/lost owners must not persist pages or checkpoints. Observation failures expose stale/incomplete coverage and retry without changing trading intent or bypassing existing DB/risk protections. Poll per account, not per FE request or once per pair when an account bill page suffices.

#### P7.4 — Coverage and existing FE APIs

- [x] Backfill with a resumable cursor plus an overlapping time window for delayed bills; advance coverage only for fully committed windows, never to the restart wall clock by default. Respect documented endpoint retention, pagination limits and Demo/site availability. Use supported historical bill reads for older gaps; report an uncovered interval when it cannot be retrieved. Automated archive/file export is outside this scope.
- [x] Extend existing `/status` reporting and `/statistics/daily` models/handler Swagger annotations with last successful sync, pending catch-up and covered/uncovered interval semantics. Keep spot funding `not_applicable`, perpetual funding separate, and daily buckets based on UTC event instants with the configured display timezone. Validate settlement currency for supported USDT contracts; do not sum unrelated currencies or treat unavailable data as zero. FE reads must stay observational and reuse stored results/cache.

OKX documents recent bills with `billId` pagination and a separate historical
bills endpoint. Limits and field semantics must be verified during implementation;
the recent endpoint alone does not promise complete lifetime history.
[OKX account bills](https://my.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-7-days),
[OKX pagination guidance](https://www.okx.com/docs-v5/trick_en/#pagination).

#### P7.5 — Green verification and acceptance

- [x] Pass targeted unit tests and disposable real-PostgreSQL tests for exact amounts, original currencies, event times, migration replay, transactional cursor advancement, uniqueness and owner fencing. Keep upstream file-mode regressions green.
- [ ] Extend the existing internal-network E2E simulator: stop/expire with a held position, inject multiple funding bills while stopped, restart, replay pages and verify the database plus daily API totals exactly. Include closed-position catch-up, delayed bills, owner handover, unavailable/history-limited responses and DB failure without new exchange trading mutations.
- [ ] Run the existing local load probe with the funding reader active. Verify bounded REST/DB calls across many pairs and FE polls, no N+1, no duplicate totals, no leaked tasks/connections and unchanged trading/API budgets. Save sanitized evidence, update this checklist/progress and commit.
- [ ] Finally run opt-in OKX Demo acceptance using the completed implementation: compare an actual bill with DB/daily totals, repeat ingestion and restart to prove deduplication/backfill. If no real settlement is available, keep this item open. The original-image 07:00 watcher does not certify P7. No Cloud Run staging or exchange load test is required.

## 8. Scope kept out

Sensitivity tuning, automatic trend discovery, automatic
spot/perpetual hedging, TWAP inventory execution, pause/resume, live duration
editing, a separate session-history/per-session PnL subsystem, general economics/
reservation frameworks, Redis, frontend WebSocket streaming, user/RBAC management and writable YAML
APIs are outside this implementation. Current session state in `/status` is enough
for FE control; existing order/fill history remains available.

Completion means the three scenarios, timed/random sessions and authenticated FE
controls work on **spot, perpetual and both**, using the existing engine, with
Red/Green evidence, passing local integration/E2E/load gates and final OKX Demo
integration results. Existing ownership, persistence, recovery and risk protections
remain required; unavailable Demo/LIFE checks must remain explicitly open. P7 is
an additional required funding-accounting follow-up before calling the new
idle/restart funding behavior complete; prior P1–P6 evidence does not mark it done.
