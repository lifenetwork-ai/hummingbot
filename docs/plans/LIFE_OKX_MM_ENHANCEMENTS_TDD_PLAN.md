# LIFE / OKX MM: Incremental Enhancements — TDD Plan

Date: 2026-10-26 (Asia/Ho_Chi_Minh).
Status: ready for staged implementation after compatibility/availability review;
implementation has not started. All delivery checkboxes below remain open.
Baseline: `1867159e7`.
Implementation priority: service availability, explainable trading decisions and
automatic recovery from classified transient failures. Compatibility and
availability review updated on 2026-10-26.

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
| E0 | Failure isolation, task supervision and observable automatic recovery | First |
| E1 | Strict reference-deviation and manual sideways-range guards | After E0 |
| E2a | Wire existing drawdown protection into MM configuration | After E1 |
| E2b | Bounded rolling spot USDT outflow limit | After E2a |
| E2c | Bounded listing bootstrap, then market guard | After E2b; requires its spending protection |
| E3 | Optional spot inventory skew using existing calculations | After E2c |
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

### Compatibility review against the baseline

The phases are extensions, not drop-in configuration changes. The following
integration gaps must be resolved in their owning phase before acceptance:

| Baseline behavior | Required adaptation |
|---|---|
| `BotOwnership._check_mm_authority()` calls one boolean `mm_ready_check()` for all placements | Keep shared readiness for feed/session failures; add a small order-aware decision for BUY-only spending restrictions. Exhausting a BUY cap must not revoke the whole session or block permitted SELL/cancel operations |
| Runner global drawdown iterates current controllers; orchestrator performance skips held positions outside current strategy markets | E2a must explicitly cover retained disabled-market exposure or reject admission for missing valuation. Passing thresholds into the runner alone is insufficient |
| Runner controller drawdown only selects active executors with `not is_trading`, and stop actions default to closing positions | E2a must use the MM position-preserving service stop path and handle partial-fill remainders, preserving legacy caller behavior |
| `orders` / `trade_fills` have no account/environment identity; one `BOT_ID` per DB is already enforced | E2b must establish and enforce a single account/environment binding for the existing DB before interpreting its fills. Do not claim multi-account attribution from market/pair filters |
| PMM V1 derives skew range from one base-denominated `order_amount`; LIFE uses weighted quote allocations | E3 must adapt the range to the actual baseline LIFE level quantities. Copying the method or using one arbitrary level amount changes its meaning |
| `update_reference()` replaces `processed_data`, sets multiplier to 1, and is called by runtime/final-send readiness checks | E1/E5 need one coherent observation/policy composition path. Repeated checks must not reset spread policy, advance a recovery timer by call count, or perform expensive indicator work |
| Public books and private order updates have no shared atomic snapshot; candle feeds include the forming candle | E1 must treat own-order subtraction as conservative observation with explicit ambiguity; E5 must select completed intervals explicitly rather than use the last row unconditionally |
| `ServiceHost.run()` lets an `observe_once()` exception reach the process exit handler; reporting projection and reconciliation claiming are called on this path | E0 must isolate optional observation failures and classify critical failures before deciding whether to retry, wait, HALT or exit |
| `BotStateStore.publish()` writes heartbeat/control receipts and daily balance observations in one transaction | E0 must keep control/receipt correctness while preventing a malformed optional balance observation from aborting an otherwise healthy control publication |
| Controller `on_start()` can fail before its control loop; a RUNNING status alone does not prove the task is alive | E0 must observe task completion/progress and expose failed initialization, rather than leave an unexplained readiness wait |

These gaps do not authorize replacing the existing guard, tracker, accounting or
execution systems. Add the smallest tested adapters at these extension points.

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
| Market quote guards | `quote_guards.enabled` defaults false; enabled `mode` is `strict` by default, or explicit `bootstrap_then_market` after E2c; deviation/range bounds, recovery delay and tighter resume boundary |
| Listing bootstrap | Existing market `initial_anchor`, mandatory duration, anchor-relative price band, bootstrap quote budget and E2 spending/exposure limits; no usable trading defaults |
| MM drawdown | Existing `max_global_drawdown_quote` and `max_controller_drawdown_quote`, explicitly wired to the runner with declared scope |
| Spot cash consumption | `spot_outflow.window_seconds` and `max_net_usdt_outflow`; an absolute USDT amount, not a percentage with an undefined denominator |
| Spot inventory | `inventory_skew.enabled`, `target_base_pct`, `inventory_range_multiplier`, reusing existing meanings where compatible |
| Spread policy | `spread_adjustment.enabled`, candle interval/window, bounded multiplier, optional fee-aware minimum spread; unavailable candles use an explicitly configured static fallback, or `wait` |

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
Preserve the stored config hash of an older session; do not recompute it from a
model dump that now includes new default fields. New sessions hash their fully
resolved policy under an explicit schema/version convention.

Durable risk limits, event history and these admin controls target the existing
PostgreSQL service mode. Preserve the original file-mode defaults; reject an
unsupported durable-policy/file-mode combination rather than implying that an
in-memory state survives restart or introducing another file persistence path.

Expose effective policies through `/profiles`; extend `/status` with guard
decisions, measurement time, freshness, limit scope and recovery reason. Money
stays Decimal strings and timestamps stay UTC `Z`. Extend handler annotations,
generate with `make swagger`, and validate with `make swagger-check`.

## 3A. E0 — Availability and failure handling

Service availability and quote availability are separate. A reachable admin API
with paused quotes is a valid degraded state; a live process with a dead trading
task and no explanation is not. Reuse existing service/connector loops and task
handles; add small supervision/classification helpers. Handle errors at the
smallest responsible boundary instead of retrying the complete trading loop.

### Failure policy

| Condition | Trading behavior | Service and recovery behavior |
|---|---|---|
| Dashboard serialization, optional liquidity sampling or daily snapshot validation fails while ownership/recorder remain healthy | Continue permitted quoting | Keep API/health and required control publication alive; mark the observation unavailable/stale and retry optional work |
| Funding observation or observational reconciliation fails | Keep existing trading policy; missing funding is not a new loss rule | Preserve existing retry/coverage behavior; a real shared DB/ownership failure still follows the critical-failure policy |
| Required book/account/reference data is stale or its read/reconnect receives a transient timeout/429/5xx | Close affected quote admission and cancel unsafe working quotes through the existing lifecycle | Keep service alive; reuse connector reconnect/throttling and automatically resume after fresh data passes recovery checks |
| Optional volatility candles or skew input is unavailable | Use explicitly configured static-spread/neutral-skew fallback only while mandatory price, balance, exposure, fee and ownership checks pass | Expose the fallback and recover the enhancement automatically |
| Rolling BUY allowance is exhausted | Block/reconcile BUY quotes; allow funded SELL when other guards permit | Recompute as fills/window change and automatically re-enable BUY when capacity is available |
| Price/range guard fails, or bootstrap expires without qualified market data | Wait with quotes cancelled, retain positions | Keep API/health alive; recover only on valid market data; never widen limits or renew bootstrap automatically |
| Operator stop or finite session expiry | Stop quotes as requested | Keep service alive; report `operator_stopped` or `session_expired`; recovery cannot restart a stopped/expired session |
| Drawdown breach, account binding mismatch, unresolved order/account mutation or unclean recovery | Preserve durable HALT and block new risk | Keep management/read-only observation available when safe; expose the required operator action; connection recovery cannot clear HALT |
| Owner connection lost, recorder rollback, critical persistence failure, or unsafe/incomplete cleanup | Fail closed using existing fencing/cleanup rules | Controlled exit is allowed when required by ownership/recorder invariants; record the reason and retain uncertainty for the next process |
| Invalid configuration/credentials or permission rejection | Reject command or block affected startup | Report actionable configuration/auth error; do not retry it as a temporary data problem |

Unknown exceptions in order-producing tasks close admission and surface as internal
failures. Retry only when the operation was read-only or proven unsent and cleanup/
state remain valid. Never resubmit an ambiguous mutation. `CancelledError` and
shutdown signals must propagate. The current crash/DB-loss/manual-recovery boundary
remains; E0 does not promise continuation through unknown execution state.

Separate optional report projection/daily snapshot handling from required owner,
control-version and command-receipt publication. Use a narrow validated path or
transaction savepoint where appropriate. A failed connection, recorder write or
critical commit cannot be swallowed as a reporting error. E4's state-changing
events remain atomic with their corresponding transition.

Reuse existing throttlers/backoff first. New safe read retries start at 1 second,
double up to a 30-second scheduled-delay cap and include jitter within that cap;
respect upstream retry instructions. Bound each attempt by the existing request
timeout, allow one attempt per dependency at a time, and reset backoff on success.
Continuous outage remains a visible waiting state with bounded memory. A retry
must not occupy the owner/health loop or block stop/switch. Do not extend data
freshness thresholds to hide an outage.

Supervise task completion and meaningful progress for the service observer,
controller startup/control, connectors and optional collectors. Classify early
`on_start()` failures even if status still says RUNNING. Reuse task handles or
retain the existing scheduled task where absent. Recover read-only initialization
failures in place only when no unresolved mutation/runtime state remains; do not
restart executors with unknown order effects.
Use a control/observer progress deadline of `max(5 seconds, 3 * update_interval)`.
A declared bounded read/backoff wait carries its own deadline and reason; it is
not a dead task. Detect completed/failed tasks by the next observer cycle. Existing
HTTP liveness/platform supervision handles a wholly blocked event loop; a
heartbeat from another thread must not falsely certify its progress.

Extend authenticated status with a bounded current-incident record: stable
`reason_code`, scope/market/side, severity, sanitized detail, first/last occurrence,
last healthy observation, retry count/next retry, active fallback and
`operator_action_required`. Report service/observer progress separately from
whether BUY/SELL are allowed. Log transitions immediately and summarize repeated
failures at most once per minute; no messaging integration is required.

`/live` must reflect a responsive process and progressing required loops. It stays
healthy for deliberate stop, feed wait or risk HALT while those loops respond.
`/ready` remains trading readiness; do not use it as liveness or hide admin access
behind it. `/startup` still requires valid schema/bootstrap. If PostgreSQL status
cannot be read while the process still serves, return explicit unavailable/stale
state with observation time; never invent command success or fresh ownership.
Terminal exits log the reason before cleanup when possible; abrupt OS kills remain
observable through the existing runtime marker and platform logs.

- [ ] **Red:** optional report serialization/validation, snapshot write and reconciliation-claim failures do not terminate a healthy owner loop or falsify command receipts.
- [ ] **Red:** transient read-only startup failure, stale WS feed, reconnect, timeout and rate limit recover without a second controller task or duplicate order.
- [ ] **Red:** failed controller startup/dead task becomes visible; waiting/retry retains API access, accepts stop/switch and preserves deadlines.
- [ ] **Red:** DB/lease loss, recorder rollback and uncertain mutation still fail closed; generic retries cannot clear HALT or replay an ambiguous placement.
- [ ] **Green:** implement narrow failure isolation, task progress checks and structured reasons on existing service/config/API paths; preserve critical publication transactions.
- [ ] **Verify/commit:** unit, real-PG and image fault tests prove process survival for optional/transient observation failures and classified recovery/exit for critical failures. Document reason codes and recovery actions.

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
Private-order and public-book timestamps do not establish an atomic common
snapshot. Define conservative handling of pending creates/cancels, depth smaller
than tracked own remainder and fill/book update reordering; do not promise exact
external liquidity reconstruction from these streams.

An empty/own-only listing book cannot satisfy `strict` market-price qualification.
E2c adds explicit `bootstrap_then_market` for this situation after spending limits
are available. E1 delivers strict mode first; a configured `initial_anchor` alone
must not silently bypass it. Surface waiting and the required policy/configuration
in status rather than terminating the service.

Transient failures block new quotes and request position-preserving cancellation.
Resume only after fresh, valid observations remain within the tighter resume
boundary for the configured delay. A restart resets this recovery delay
conservatively. In `both`, a required market failure blocks both as today, and
neither deadline is extended. Recovery HALT always takes precedence.

- [ ] **Red:** threshold equality/exceedance, invalid Decimal inputs, manual range bounds, breakout, stale/crossed/empty/own-only books and insufficient filtered depth.
- [ ] **Red:** own partial fills/cancel races, inconsistent book/order snapshots, both-market failure, expiry during recovery and restart without recapturing anchors.
- [ ] **Green:** implement a bounded observation/decision helper; reuse cancellation and feed subscriptions; recheck authorization after throttling so queued new orders cannot ignore a failed guard.
- [ ] **Green:** separate sampling/state transitions from repeatable readiness checks; make recovery depend on elapsed time and distinct valid observations, not the number of calls to `update_reference()`.
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
The baseline controller stop branch does not satisfy this contract: it excludes
partially trading executors and uses default stop actions. Add an MM-specific
adapter to the existing service risk-stop path before enabling these thresholds.

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

The first delivery supports the existing single-bot DB with one verified OKX
account/environment binding persisted in the bot's risk state. Reject a changed
account or demo/live environment against that bound history; market/pair filters
cannot separate them in the current recorder schema. Legacy unbound history is
unknown until its origin is established or it has aged outside a fully observed,
reconciled window. Never retroactively label it using the current credentials.
Multi-account reuse of the same recorder history would require explicit order
attribution and is outside this phase.

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
Key reservations by the existing client order identity. Rechecking the same order
must count its reservation once, not reserve it again or add it as another new
proposal. A BUY-only denial uses order-aware authorization; it must not set the
global boolean readiness gate false. Preserve existing ownership/HALT checks for
every permitted order and cancellation.

Derive durable cashflow from existing rows, using bounded incremental reads and
an in-process projection. Rebuild before admission after restart. Do not create
a duplicate fill table, scan all history per tick or query OKX from FE polls.
Use existing indexes first; add a simple idempotent index only if query evidence
requires it. Recorder/DB uncertainty keeps new spending blocked.

- [ ] **Red:** exact Decimal buys/sells, USDT fees/rebates, base/third-currency fees, window edges, duplicate/out-of-order fills and multiple same-timestamp fill identities.
- [ ] **Red:** bound account/environment mismatch, unbound legacy history and restart cannot mix demo/live or another account's cashflow into the allowance.
- [ ] **Red:** concurrent levels, partial fills, cancel timeout, delayed recorder writes, throttled placement and reserve-to-fill handoff cannot bypass the cap.
- [ ] **Red:** repeated pre-intent/final-transport authorization counts one reservation once; BUY-only rejection does not block a funded SELL or require recovery for a proven unsent order.
- [ ] **Red:** switch/restart/market-mode changes retain the rolling window; disabled behavior is unchanged; cap exhaustion blocks buys while funded sells remain possible.
- [ ] **Green:** implement a small projection/reservation extension on existing recorder/owner paths and expose used/reserved/remaining capacity plus freshness.
- [ ] **Verify/commit:** real-PG reconstruction/deduplication and simulator accounting tests pass; measure query count and hot-path cost with large historical data.

### E2c — Listing bootstrap without outside two-sided liquidity

Support `quote_guards.mode: strict | bootstrap_then_market`. Strict remains the
default when the guard is enabled. Bootstrap is an explicit policy for a listed,
tradable instrument with a fresh but empty/own-only book. It cannot bypass stale
feeds, missing instrument metadata, unhealthy account/DB state or recovery HALT.
It depends on E2a/E2b; do not enable bootstrap trading before those limits exist.

Require an explicit positive `initial_anchor` for each selected market, existing
absolute min/max prices, a maximum anchor-relative move, finite bootstrap duration
and quote budget, spot outflow/inventory limits and applicable perpetual gross/
side/leverage limits. Bootstrap quote allocation cannot exceed the ordinary market
budget. For spot, require bootstrap duration no longer than the outflow window so
that its spending cap covers the whole bootstrap episode. No production amounts
or permissive limits are silently supplied by defaults.
Require explicit outside-depth, maximum market spread and qualification-duration
settings for the transition. If a qualified market already exists, use strict
market validation directly. A fresh usable market showing a price/range violation
must block quoting; lack of qualification dwell cannot be used to bypass that
observed violation under bootstrap.

| Guard state | Quote behavior and transitions |
|---|---|
| `bootstrap` | Sideways quotes around configured anchor; follow profiles use the existing return formula with that LIFE anchor and fresh reference inputs, constrained by the bootstrap price band and normal risk checks |
| `market_guarded` | Enter when the known-order-filtered target book meets configured depth/spread/freshness requirements continuously for the qualification period; apply normal deviation/range checks |
| `waiting` | Cancel quotes when bootstrap expires or a mandatory check fails; continue observing/retrying. Qualified market data can enable market-guarded quoting later if session authority is still valid |

These are guard states, not new scenarios or public pause/resume commands. Keep
them in bounded existing bot/session risk state. Persist bootstrap start/deadline,
immutable original anchor/band and whether market qualification has occurred,
scoped to the bound bot/account/environment/instrument and market. Start the
bootstrap clock at first quote admission and commit it before any order send.
Use the earlier of its deadline and the current session/step deadline. Restart,
random rotation or a fresh session ID cannot renew that instrument's episode or
rebase its original band to the bot's own quotes. Re-arming an exhausted bootstrap
is not an automatic operation or an endpoint in this delivery.

During bootstrap always use the configured LIFE anchor, even if the raw book now
contains the bot's quotes. Reference basket anchors retain existing within-step
persistence. Transition to market guarding changes validation, not the saved
reference formula/anchor. Once qualified, loss of outside liquidity waits for
market recovery; it never silently returns to bootstrap. In `both`, both market
legs must satisfy their selected guard policy; do not silently trade spot alone.
For sideways with no qualified outside price, enforce its manual range on the
anchor and every quote; expose market breakout detection as unavailable rather
than using own quotes as evidence that the market remains inside the range.

Bound all quote prices, including outer levels after spread adjustment, by the
bootstrap band and absolute bounds. Pass the bootstrap expiry into final transport
authorization/request expiry alongside the existing session deadlines. The UI
reports `configured_anchor` / `reference_model` / `market_book`, bootstrap time
remaining, qualification progress and the blocking reason. Model-based bootstrap
prices are not labelled market-confirmed or guaranteed fair value.

- [ ] **Red:** empty/own-only fresh listing books can bootstrap with complete policy; missing anchor/limits, stale feeds, unavailable instruments or HALT cannot.
- [ ] **Red:** quote levels, both-market budgets, partial-fill reservations and bootstrap duration respect all E2 limits; widening spread cannot escape the anchor band.
- [ ] **Red:** own quotes cannot move the original anchor or establish external qualification; intermittent depth resets qualification dwell time.
- [ ] **Red:** expiry while throttled, restart, random/manual switch and revision changes cannot extend/re-arm bootstrap; later fresh market data recovers without a new bootstrap episode.
- [ ] **Green:** extend existing state/config/admission paths and expose the guard state/reason; persist qualification/deadline before changing authorization.
- [ ] **Verify/commit:** real-PG replay and offline listing-to-market transitions pass with the independent simulator, including both-market readiness and no forced fills.

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
If only the optional skew calculation/input is unavailable, use neutral multipliers
and report `inventory_skew_unavailable` while mandatory price, exposure and balance
checks remain healthy. Missing mandatory account data still blocks quote admission.

Apply skew to base allocations, scale down if needed to stay inside the existing
total quote budget, then apply outflow/exposure/balance caps and exchange rounding.
Never redistribute a rejected allocation back onto the blocked side or round up
through a cap. Zero-sized/below-minimum levels are skipped. Perpetual keeps its
current long/short/gross limits and does not use spot ratio skew.

LIFE has no PMM V1 `order_amount`. Derive the skew range from the sum of the
unskewed LIFE level amounts at their actual proposed prices, using the existing
quote-to-base conversion. Reuse the compatible ratio/interpolation calculation;
do not copy its base-order sizing assumption. Preserve the upstream caller's
numerical behavior while keeping final LIFE quantities and cap checks in Decimal.

- [ ] **Red:** neutral/underweight/overweight inventory, zero/invalid valuation, empty balances, stale account data and shared-collateral observations.
- [ ] **Red:** optional skew failure degrades to neutral sizing and recovers without restarting the controller; mandatory account/price failures still block admission.
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

When volatility adjustment is enabled, require enough fresh completed candles for
dynamic sizing. Candle warmup/outage uses the explicitly configured static fallback
spreads (`on_unavailable: static`) or waits (`on_unavailable: wait`). Validate that
static fallback spreads remain within quote bounds and satisfy any enabled fee
floor; do not fall back to an unvalidated multiplier of 1. Enabling dynamic mode
with static fallback requires those spreads in YAML, with no invented production
defaults. All mandatory price/account/risk checks remain active. Expose fallback
state and automatically restore dynamic policy after valid observations return.
Valid zero volatility uses the configured floor. Bound the multiplier, keep levels ordered,
validate resulting spread/price bounds and retain total quote capital/exposure
limits. Evaluate on new completed candles and reuse subscriptions; do not scan a
DataFrame or fetch REST data for every quote/FE request.
Current candle rows may still be forming; choose completed intervals using the
provider's timestamp/interval semantics and test boundary/reordered updates.
Compose the cached multiplier/fee floor with reference updates so a service or
send-gate `update_reference()` call cannot overwrite it with multiplier 1. Final
checks validate freshness/version of the same composed decision without running
NATR or copying the full book again for each order.

- [ ] **Red:** fee currencies/rebates, unknown fee basis, one-sided/two-sided spread semantics and Decimal/tick-rounding boundaries.
- [ ] **Red:** insufficient/stale/flat candles, NaN/infinite indicators, shocks, multiplier caps and final quote limits.
- [ ] **Red:** candle warmup/outage selects the declared static/wait policy, preserves an enabled fee floor and returns to dynamic quoting without process/controller restart.
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
- [ ] Exercise E0's failure matrix through the full service process: inject optional report/snapshot errors, read timeouts, WS loss, transient startup failure, dead controller task and candle outage. Assert expected API/process availability, classified quote restrictions and automatic recovery; no unexplained exit or indefinite unreported readiness wait is accepted.
- [ ] Run bootstrap on a fresh empty/own-only market, then introduce qualified outside depth. Verify bounded quoting, qualification transition, expiry/wait recovery and persisted state across restart/profile switches; never inject fills merely to make the bootstrap qualify.
- [ ] Extend the healthy workload with valid external book depth/candle data and bounded event/liquidity reads. Certify enabled features rather than disabling guards to obtain a pass; keep deliberate guard/fault windows separate from healthy latency/error gates.
- [ ] Measure query counts, synchronous CPU, event-loop lag, DB checkout and reporting cost with the existing 100,000-order/fill fixture. Confirm no per-level/per-row N+1, unbounded caches or per-request exchange calls; optimize demonstrated regressions only.
- [ ] Run short diagnostics during development, then the unchanged full mixed/burst and sixty-minute zero-error soak gates once the final implementation/image is stable. Preserve original resource, latency, growth and accounting budgets; record any fixture/read-mix additions explicitly.
- [ ] Finally run opt-in OKX Demo integration on available instruments: enabled guards, normal maker quoting, switch/stop/restart, actual order/fill/fee comparison and both-market behavior. Use local simulation for deterministic risk breaches that Demo cannot safely/reliably reproduce; label those scopes separately.
- [ ] Update operational guides, handler annotations and generated Swagger; record sanitized results under `reports/`, large raw artifacts under ignored `build/`, and link evidence beside completed phase checkboxes.
- [ ] Record residual limitations and effective policy settings, mark only verified items done, and commit the completed delivery. A passing test gate does not certify strategy profitability or LIFE instrument availability.

During development, use targeted verification after each change rather than a
new hour-long run for every edit. E6 is new-image acceptance for these additions;
it is not a request to reproduce the previously closed timeout incident.

Availability acceptance is distinct from the healthy-load latency gate:

- Optional observation failures must cause zero service restarts and zero changes
  to otherwise valid trading intent. API/control remain usable while their required
  DB path is healthy; failed observation fields explicitly lose freshness.
- Required-feed failures must be detected within configured data-age/progress
  bounds plus one owner observation cycle. Cancellation acknowledgement latency
  remains measured separately; unavailable exchange cancellation is not reported
  as confirmed cancellation.
- After required reads/feeds actually recover, resume within the existing request
  timeout plus the capped retry delay, configured recovery dwell and two owner
  cycles, provided the session is unexpired and no durable HALT exists. A server
  rate-limit delay is visible in `next_retry_at`, not an unexplained timeout.
- Every wait, fallback, task failure and intentional stop has a reason and an
  automatic/operator recovery classification. A dead task is detected within the
  E0 progress deadline; waiting tasks emit progress without pretending that market
  data is fresh. Verify both control-loop deadlines and declared read/backoff waits.
- Critical DB/ownership/mutation faults still meet the existing fail-closed
  assertions. Expected HALT/controlled exit in these cases is reported separately
  from unexpected service unavailability. No test clears HALT automatically to
  manufacture an uptime result.
- Measure service uptime and quote availability separately, with unavailable
  intervals and guard reasons retained. Do not remove guarded/waiting periods
  from the total session denominator to inflate quote availability.

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
