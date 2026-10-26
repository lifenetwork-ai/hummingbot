# LIFE / OKX MM: Incremental Enhancements — TDD Plan

Date: 2026-10-26 (Asia/Ho_Chi_Minh).
Status: proposed; implementation has not started. All delivery checkboxes below
remain open. Baseline: `1867159e7`.

Extend the completed [LIFE / OKX implementation](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md)
and [PostgreSQL / Cloud Run port](POSTGRES_CLOUD_RUN_PORT_PLAN.md). This plan selects
the useful additions from the Campaign-Driven MM v2 proposal without rebuilding
session management, trading execution, persistence or the admin API.

The existing full-image [sixty-minute acceptance gate](../../reports/life-mm-soak-retest.md)
is closed. Its historical timeout reproduction follow-up stays closed. Changes
delivered here require their own verification; they do not invalidate or reopen
the historical checklist. Cloud Run packaging/local verification is available;
this plan does not claim an existing Cloud Run deployment.

## 1. Scope and sequence

Keep `follow_market`, `sideways`, `follow_token`, `selection: fixed | random`
and `market_mode: spot | perpetual | both`. Preserve the current reference-return
formula, finite deadlines, persisted anchors and maker execution. A UI may label
a session a campaign; there is no second campaign runtime or ID hierarchy.

| Phase | Addition | Delivery order |
|---|---|---|
| E1 | Reference-deviation and manual sideways-range guards | First |
| E2a | Wire existing drawdown protection into MM configuration | After E1 |
| E2b | Bounded rolling spot USDT outflow limit | After E2a |
| E3 | Optional spot inventory skew using existing calculations | After E2b |
| E4 | Session/profile transition history and small liquidity KPI API | After E3 |
| E5 | Optional fee floor and volatility spread adjustment | After E4 |
| E6 | Final image E2E, load/soak, then opt-in OKX Demo acceptance | Last |

Each phase uses Red → Green → refactor, updates this checklist with evidence,
and ends with a local commit before the next phase. If an upstream behavior or
missing data prevents a correct implementation, record the exact blocker and
report it; do not silently weaken the requirement or skip ahead.

## 2. Reuse map and boundaries

| Existing component | Use for this work |
|---|---|
| [LIFE controller](../../controllers/market_making/life_mm.py) | Quote validation, exposure caps, freshness, post-only entries and position-preserving cancellation |
| [MM base](../../hummingbot/strategy_v2/controllers/market_making_controller_base.py) / [PMM simple](../../controllers/market_making/pmm_simple.py) | Price levels, quote allocation, refresh and existing position executors |
| [PMM V1](../../controllers/generic/pmm_v1.py) | Inventory-skew calculation; extract a small compatible helper if needed, not the entire controller/executor path |
| [PMM dynamic](../../controllers/market_making/pmm_dynamic.py) / [market data provider](../../hummingbot/data_feed/market_data_provider.py) | Candle access and volatility calculation patterns; keep LIFE's reference and execution semantics |
| [V2 runner](../../scripts/v2_with_controllers.py) / [orchestrator](../../hummingbot/strategy_v2/executors/executor_orchestrator.py) | Existing reports, drawdown logic and held positions; no second PnL engine |
| [MM session](../../hummingbot/core/mm_session.py) / [service](../../hummingbot/cli/service.py) | Effective configuration, timers, admission, both-market readiness and switching |
| [Bot state store](../../hummingbot/model/bot_state_store.py) / [ownership](../../hummingbot/core/bot_ownership.py) | Transactions, checkpoints, ownership, recovery and HALT |
| [Submission guard](../../hummingbot/core/order_submission_guard.py) / [recorder](../../hummingbot/connector/markets_recorder.py) | Final send authorization, durable order intent, actual fills and fees |
| [Admin handlers](../../hummingbot/cli/admin_api.py), [models](../../hummingbot/cli/admin_models.py), [store](../../hummingbot/model/admin_store.py) | Basic Auth, command receipts/CAS, bounded reads and handler OpenAPI annotations |
| [Reporting](../../hummingbot/model/admin_reporting.py) / [observations](../../hummingbot/cli/admin_observations.py) | Existing account observations, daily fees/funding and report caches |

No new service, scheduler framework, broker, Redis dependency, connector, order
tracker, matching engine or generic risk framework is needed. Small pure helpers
are appropriate when they let existing callers share tested calculations.

The current recovery boundary remains: an uncertain crash/transport outcome can
require operator recovery. Neither improved feed health, an API switch nor a
new profile may clear HALT. Stop/switch/expiry cancel quotes and preserve filled
spot inventory and perpetual positions; this work must not introduce automatic
market liquidation or market rebalancing.

## 3. Configuration and API contract

Strategy policy belongs in the existing YAML/catalogue and effective session
snapshot. Infrastructure settings continue through the existing `.env` loader
and [central defaults](../../hummingbot/client/config/defaults.py). Do not add
duplicate YAML/env controls or a `LIFE_` prefix. No new environment variable is
required by this plan initially.

All new trading policies are opt-in for backward compatibility. Disabled settings preserve
current behavior; the new-feature acceptance runs explicitly enable the features
being certified. Example YAML must identify disabled/default values and must not
present arbitrary risk thresholds as suitable production settings.

Proposed policy fields below are design names, not supported configuration yet:

| Area | Proposed settings and semantics |
|---|---|
| Market quote guards | `quote_guards.max_reference_deviation_bps`; optional `sideways_range.lower_price/upper_price`; recovery delay and tighter resume boundary |
| MM drawdown | Existing `max_global_drawdown_quote` and `max_controller_drawdown_quote`, explicitly wired to the runner with declared scope |
| Spot cash consumption | `spot_outflow.window_seconds` and `max_net_usdt_outflow`; an absolute USDT amount, not a percentage with an undefined denominator |
| Spot inventory | `inventory_skew.enabled`, `target_base_pct`, `inventory_range_multiplier`, reusing existing meanings where compatible |
| Spread policy | `spread_adjustment.enabled`, candle interval/window, bounded multiplier and optional fee-aware minimum spread |

Validate finite Decimal values, units, ordered bounds, valid ratios and compatible
market/scenario combinations. Reject spot-only options on perpetual; do not
silently ignore them. Keep configured exposure caps authoritative after every
adjustment. Extend `MMProfile` only for profile-specific policy; market budgets
and market-specific limits remain in market templates. Resolve defaults once
and include the effective policy in the session config hash.

Initial delivery does not expose arbitrary config editing. Existing admin
start/switch selects the deployed policy and supported runtime fields. Policy
changes use the existing explicit switch lifecycle, with idempotency and expected
version checks. A running session's saved configuration remains authoritative
across restart/revision changes. Decode older saved sessions with new features
disabled, without resetting anchors, deadlines or risk checkpoints.

Durable risk limits, event history and these admin controls target the existing
PostgreSQL service mode. Preserve the original file-mode defaults; reject an
unsupported durable-policy/file-mode combination rather than implying that an
in-memory state survives restart or introducing another file persistence path.

Expose effective policies through `/profiles`; extend `/status` with guard
decisions, measurement time, freshness, limit scope and recovery reason. Money
stays Decimal strings and timestamps stay UTC `Z`. Extend handler annotations,
generate with `make swagger`, and validate with `make swagger-check`.

## 4. E1 — Price and manual-range guards

Current code checks data age, positive/non-crossed books and absolute quote bounds.
It does not bound the computed reference against a usable current target-market
price, or detect a sideways breakout. Add these checks in the existing controller
and admission/send path rather than a separate price engine.

When enabled, compare the computed reference with a qualified target-market
midpoint: `abs(reference / market_mid - 1) * 10000`. Keep absolute min/max checks
on every resulting quote. A manual sideways range checks the observed target
market, not just its saved anchor. No automatic range discovery is included.

Qualification must address the bot's own quotes: reuse order-book levels and known
exchange-acknowledged own orders to exclude their remaining quantity at matching
prices. If two usable sides or consistent order/book observations are unavailable,
report `market_price_unavailable` and block quoting. A known-order-filtered book
is an observation, not proof of an independent fair value. Do not claim to exclude
unknown/manual orders or quotes from other accounts. Bound book traversal and
report insufficient coverage instead of scanning indefinitely.

An empty/own-only listing book cannot satisfy an enabled market-price guard.
The existing explicit `initial_anchor` remains a bootstrap option when that guard
is disabled; it must never silently bypass an enabled guard. This operational
trade-off must be visible in YAML guidance and status.

Transient failures block new quotes and request position-preserving cancellation.
Resume only after fresh, valid observations remain within the tighter resume
boundary for the configured delay. A restart resets this recovery delay
conservatively. In `both`, a required market failure blocks both as today, and
neither deadline is extended. Recovery HALT always takes precedence.

- [ ] **Red:** threshold equality/exceedance, invalid Decimal inputs, manual range bounds, breakout, stale/crossed/empty/own-only books and insufficient filtered depth.
- [ ] **Red:** own partial fills/cancel races, inconsistent book/order snapshots, both-market failure, expiry during recovery and restart without recapturing anchors.
- [ ] **Green:** implement a bounded observation/decision helper; reuse cancellation and feed subscriptions; recheck authorization after throttling so queued new orders cannot ignore a failed guard.
- [ ] **Green:** expose guard reason, source quality and observation timestamps; test that cancellation remains permitted while new placement is blocked.
- [ ] **Verify/commit:** focused controller/config/guard tests, real-PG lifecycle coverage and simulator fault cases pass; update guide/Swagger and record evidence.

## 5. E2 — Loss and spending limits

### E2a — Existing drawdown protection

Reuse the runner's report-based drawdown checks and ownership checkpoints. The
current MM effective runner config does not wire the drawdown thresholds through.
First prove the report scope and initialization/recovery behavior, then connect
the configured limits. Fix only demonstrated gaps with Red/Green tests.

The declared metric is **bot trading drawdown from the covered V2 report peak**,
not exact account equity loss or exact campaign profit. Document fee/funding
coverage, existing precision limits and valuation freshness. Do not add funding
or account balances to a report unless the existing accounting basis supports
that operation without double counting.

Include retained bot exposure when a market is disabled or a profile changes.
An old cached report must not look fresh. If the required valuation cannot be
obtained through existing observations/recovery, block new risk with an explicit
coverage reason. Missing coverage is not zero PnL.

Scope the peak/stop state to the existing bot and stable market/controller identity,
not random generation or a newly assigned session ID. Start/switch/stop/restart
must not reset a breached limit. Preserve existing explicit operator recovery;
do not add an automatic risk reset endpoint. Loss-triggered shutdown must retain
positions and use the bounded service shutdown path, including partial fills.

- [ ] **Red:** threshold wiring, peak initialization, finite values, equality/breach and global/per-controller scopes against the actual existing report semantics.
- [ ] **Red:** risk-stop execution preserves positions; no unintended market closes, manual-kill restart or new sends after the stop decision.
- [ ] **Red:** random/manual switches, spot/perpetual/both changes, retained disabled-market exposure, missing valuations, clean restart and unclean HALT preserve limits/peaks.
- [ ] **Green:** extend existing config/report/checkpoint paths; checkpoint policy identity and scope without adding a second PnL ledger or silently changing legacy strategies.
- [ ] **Verify/commit:** run runner/ownership regressions and real-PG persistence tests; publish the exact measurement scope in status/docs before marking complete.

### E2b — Rolling spot USDT outflow

This limit controls spot cash consumption; it is separate from outstanding quote
allocation, drawdown and perpetual margin/exposure. Apply it per bot/account/
environment/trading pair across sessions and random steps. Switching to perpetual
and back must not erase recent spot spending.

Use committed spot fills in the UTC half-open window `[now - window, now)`:
buy consideration minus sell consideration, plus actual USDT-denominated fees
minus USDT rebates. Non-USDT fees are reported in their original currency and
are not invented as USDT cash movements. Transfers and perpetual funding are
outside this spot trading-flow metric.

Before admitting a buy, conservatively add remaining outstanding/pending buy
notional and a fee allowance to `max(0, recorded_net_outflow)`, then include the
new order. Pending sells do not fund pending buys. Unknown fee requirements,
incomplete fill recovery or uncertain orders block new buys. Funded spot sells
may continue unless another guard/HALT requires all quotes to stop.

Reuse recorder identities, intents and cancellation reconciliation for reservation
lifetime. Reserve before parallel executor submissions can consume the same
capacity; recheck at the existing final send gate. Reconcile fill-to-reservation
handoff without undercounting, and release only proven terminal/unsent remainder.
This bounds admitted risk; it cannot undo requests already in flight at a breach.

Derive durable cashflow from existing rows, using bounded incremental reads and
an in-process projection. Rebuild before admission after restart. Do not create
a duplicate fill table, scan all history per tick or query OKX from FE polls.
Use existing indexes first; add a simple idempotent index only if query evidence
requires it. Recorder/DB uncertainty keeps new spending blocked.

- [ ] **Red:** exact Decimal buys/sells, USDT fees/rebates, base/third-currency fees, window edges, duplicate/out-of-order fills and multiple same-timestamp fill identities.
- [ ] **Red:** concurrent levels, partial fills, cancel timeout, delayed recorder writes, throttled placement and reserve-to-fill handoff cannot bypass the cap.
- [ ] **Red:** switch/restart/market-mode changes retain the rolling window; disabled behavior is unchanged; cap exhaustion blocks buys while funded sells remain possible.
- [ ] **Green:** implement a small projection/reservation extension on existing recorder/owner paths and expose used/reserved/remaining capacity plus freshness.
- [ ] **Verify/commit:** real-PG reconstruction/deduplication and simulator accounting tests pass; measure query count and hot-path cost with large historical data.

## 6. E3 — Spot inventory skew

Reuse the PMM V1/legacy skew calculation through a small shared helper if needed.
Do not replace LIFE's position executor with the generic PMM V1 order executor.
Adjust proposed sizes; preserve reference pricing, session authority, maker order
type and position-preserving stop behavior.

Define the ratio from observed base holdings valued at a qualified price and
actual USDT cash balance. Label it an account inventory signal, not an isolated
campaign wallet. In `both`, do not add the same USDT across connectors or treat
perpetual equity as extra cash. Pending orders still count in existing hard caps;
available-balance checks remain the final funding constraint.

Apply skew to base allocations, scale down if needed to stay inside the existing
total quote budget, then apply outflow/exposure/balance caps and exchange rounding.
Never redistribute a rejected allocation back onto the blocked side or round up
through a cap. Zero-sized/below-minimum levels are skipped. Perpetual keeps its
current long/short/gross limits and does not use spot ratio skew.

- [ ] **Red:** neutral/underweight/overweight inventory, zero/invalid valuation, empty balances, stale account data and shared-collateral observations.
- [ ] **Red:** all proposed levels together respect quote budget, rolling outflow and available base/quote after skew, rounding and partial-fill reservations.
- [ ] **Green:** reuse/extract the compatible calculation with parity coverage for its existing caller; avoid introducing float precision loss into final order sizing.
- [ ] **Verify/commit:** disabled-path and perpetual behavior remain unchanged; controller and offline spot/both scenarios demonstrate size adjustment without extra market orders.

## 7. E4 — Transition history and useful liquidity observations

Persist transitions, not a second session engine. Add one small `mm_events` table
if existing receipts cannot represent the event: bot/session/generation, durable
event identity, type, UTC time, old/new profile, reason, config hash and bounded
payload. Save the resolved configuration once at session start/switch so past
events remain interpretable after YAML changes. Record the authenticated admin
principal or scheduler source; never store credentials.

Write session/control transitions and their event atomically. Use deterministic
event identity/sequence and a uniqueness constraint so retries and owner takeover
cannot duplicate events. Repeated guard observations do not create an event each
tick; record changes. Reuse control-row fencing and existing migrations, with
snake_case names and no `public.` qualifiers. No stored-procedure framework or
parallel audit worker is required.

Add bounded `GET /api/v1/admin/sessions/{session_id}/events`, reusing keyset cursors,
filters, Basic Auth and OpenAPI conventions. New history begins at this migration;
do not fabricate earlier random-step history from the current session snapshot.

Initial liquidity reporting adds `GET /api/v1/admin/statistics/liquidity` for
current/session observations, separately for spot and perpetual:

- Fresh exchange-acknowledged own bid/ask, remaining quoted notional and spread.
- Observed duration with live quotes on both sides, observed eligible duration
  and total elapsed time/coverage. A disconnected interval is unknown, not 100%
  uptime; a valid observation with no quotes is zero two-sided availability.
- Current guard reason, active profile/generation and measurement timestamps.

Use existing connector/tracker observations and bounded counters in the owner
loop; publish through existing status/checkpoint mechanisms. Do not issue an
exchange request for each FE read or persist order-book snapshots. Step-ending
events may include observed counter summaries; do not invent coverage for a
crashed interval. Existing fills/fees/daily endpoints remain the source for those
statistics. Historical depth time series, exact profile PnL and maker-fill-rate
denominator design are outside this phase.

- [ ] **Red:** atomic event/state rollback, idempotent admin retries, automatic random transitions, repeated profile draws and owner takeover.
- [ ] **Red:** event pagination/filter binding, auth, missing historical coverage and config snapshot preservation after later catalogue changes.
- [ ] **Red:** partial fills/cancel-pending quotes, stale observations, zero quotes, gaps, profile boundaries and restart do not inflate notional/uptime.
- [ ] **Green:** add minimal event migration/model and API projections; bound payload/counter sizes and label observed coverage explicitly.
- [ ] **Verify/commit:** migration replay/schema checks, real-PG/API tests, constant query-count checks and Swagger pass; prove FE polling adds no exchange calls.

## 8. E5 — Fee-aware and volatility-adjusted spread

Implement an optional fee floor first, then a bounded volatility multiplier using
existing candle/provider/NATR facilities. Do not import PMM dynamic's MACD price
shift or change the three reference formulas. Keep fixed base spreads as the
default behavior.

Define whether the fee floor covers the proposed maker buy/sell round trip and
reuse connector fee semantics, including currencies and rebates. A fee floor is
not a profitability guarantee: inventory mark-to-market and adverse selection
remain possible. Missing fee valuation blocks the fee-dependent policy instead
of silently treating the fee as zero.

When volatility adjustment is enabled, require enough fresh candles; warmup or
invalid data blocks that policy's quotes with a visible reason. Valid zero
volatility uses the configured floor. Bound the multiplier, keep levels ordered,
validate resulting spread/price bounds and retain total quote capital/exposure
limits. Evaluate on new completed candles and reuse subscriptions; do not scan a
DataFrame or fetch REST data for every quote/FE request.

- [ ] **Red:** fee currencies/rebates, unknown fee basis, one-sided/two-sided spread semantics and Decimal/tick-rounding boundaries.
- [ ] **Red:** insufficient/stale/flat candles, NaN/infinite indicators, shocks, multiplier caps and final quote limits.
- [ ] **Green:** integrate a small policy using existing providers/helpers; keep session anchors fixed and spot/perpetual observations separate.
- [ ] **Verify/commit:** disabled parity, simulator spread response, subscription cleanup, CPU/query cost and feed-recovery behavior pass before E6.

## 9. E6 — Verification and delivery gates

Reuse the [existing load harness and budgets](../life-mm-load-tests.md). Unit tests
exercise pure decisions; real disposable PostgreSQL proves transaction/recovery
behavior; the offline simulator supplies independent order/fill truth. No live
exchange load test or Cloud Run staging is required.

- [ ] Run relevant config, controller, runner, executor, owner, recorder, API and PostgreSQL regression suites; keep Red/Green evidence for every confirmed runtime fix.
- [ ] Build the full Cloud Run image; record immutable image identity, source/config/schema hashes and new feature settings. Verify packaged helpers, SQL and optional candle dependencies.
- [ ] Run three profiles × spot/perpetual/both through existing image E2E, with new options disabled for compatibility and representative enabled combinations for acceptance.
- [ ] Exercise deviation/breakout, spend/drawdown breaches, stale candles, concurrent commands/fills, expiry, switching, DB failure and clean/unclean restart. Verify no duplicate effects, no cap bypass and correct preserved positions/HALT.
- [ ] Extend the healthy workload with valid external book depth/candle data and bounded event/liquidity reads. Certify enabled features rather than disabling guards to obtain a pass; keep deliberate guard/fault windows separate from healthy latency/error gates.
- [ ] Measure query counts, synchronous CPU, event-loop lag, DB checkout and reporting cost with the existing 100,000-order/fill fixture. Confirm no per-level/per-row N+1, unbounded caches or per-request exchange calls; optimize demonstrated regressions only.
- [ ] Run short diagnostics during development, then the unchanged full mixed/burst and sixty-minute zero-error soak gates once the final implementation/image is stable. Preserve original resource, latency, growth and accounting budgets; record any fixture/read-mix additions explicitly.
- [ ] Finally run opt-in OKX Demo integration on available instruments: enabled guards, normal maker quoting, switch/stop/restart, actual order/fill/fee comparison and both-market behavior. Use local simulation for deterministic risk breaches that Demo cannot safely/reliably reproduce; label those scopes separately.
- [ ] Update operational guides, handler annotations and generated Swagger; record sanitized results under `reports/`, large raw artifacts under ignored `build/`, and link evidence beside completed phase checkboxes.
- [ ] Record residual limitations and effective policy settings, mark only verified items done, and commit the completed delivery. A passing test gate does not certify strategy profitability or LIFE instrument availability.

During development, use targeted verification after each change rather than a
new hour-long run for every edit. E6 is new-image acceptance for these additions;
it is not a request to reproduce the previously closed timeout incident.

## 10. Explicitly deferred

- Automatic trend classification, correlation/confidence calibration, AUTO range
  discovery and risk-aware profile selection. Newly listed tokens may lack the
  history needed to define these signals; they require a separate research scope.
- Random no-repeat and eligibility-based reselection. Current equal-probability
  draws may repeat; missing data blocks quotes rather than silently changing the
  selected strategy. No fourth random scenario or separate scheduler is added.
- Pause/resume, unlimited sessions, in-place duration edits, arbitrary hot config
  CRUD, multiple concurrent campaigns and isolated per-campaign wallets.
- Exact campaign/profile/day PnL, buy-and-hold attribution, historical depth
  analytics and an accounting engine for external transfers/manual trading.
- Spot/perpetual hedging, automated position reduction/liquidation and automatic
  clearing of recovery HALT.
- Replacing upstream execution, changing connector ownership, adding services,
  event sourcing, or introducing Redis without measured need.

Keep post-only execution and existing exchange STP behavior. Connector STP
configuration changes require a demonstrated gap and focused transport tests;
they are not a reason to build a separate exchange-matching/STP subsystem here.
