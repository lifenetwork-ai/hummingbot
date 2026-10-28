# LIFE MM controls

Maintainers configure these policies in deployed `life_mm_profiles.yaml`.
Operators choose sessions through the [FE/admin contract](fe-integration.md);
those commands do not override policy settings or reset durable bindings.
Examples below are illustrative test limits, not production recommendations.
All policies reuse the existing controller/executors, owner gates and recorder.

| Policy | Purpose | Details |
|---|---|---|
| Quote guards | Qualify outside depth; constrain model deviation and sideways range | [Configuration and recovery](#quote-guards) |
| Drawdown | Limit covered V2 report drawdown with durable peaks/HALT | [Measurement and restart](#drawdown) |
| Spot outflow | Limit rolling spot USDT consumption and pending BUY risk | [Binding and reservations](#spot-outflow) |
| Listing bootstrap | Permit a finite, explicitly bounded empty/own-only-book episode | [Clock and qualification](#listing-bootstrap) |
| Inventory skew | Adjust spot BUY/SELL sizes from observed cash inventory | [Calculation and fallback](#inventory-skew) |
| Fee/NATR spreads | Compose fee coverage and completed-candle volatility spreads | [Spread behavior](#fee-and-volatility-spreads) |

Disabled, waiting and HALT are different states. Keep their API reasons visible;
ordinary profile changes/restarts never bypass a bound policy. For technical
recovery use [PostgreSQL operations](postgres-runtime.md). Verification commands
below exercise individual policies; use the [load guide](life-mm-load-tests.md)
for whole-release acceptance. Measured outcomes belong in [reports](../reports/README.md).

## Quote guards

Guards are opt-in: deployed defaults use `quote_guards.enabled: false`. Existing
budgets, post-only execution, ownership, absolute price/exposure limits and
position-preserving cancellation remain authoritative. No new `.env` option,
service, subscription, cache backend or database migration is required.

### Quote guard configuration

Put deviation policy in each market template and manual range in its sideways
profile. Profile/market selection uses existing authenticated admin commands;
`/profiles` exposes the policy and session snapshots retain it across restarts.
This partial example uses illustrative test values around 0.1 USDT. Review actual
instrument thresholds; these are not production risk defaults.

```yaml
markets:
  spot:
    # Retain existing connector, pair, budgets and absolute limits.
    quote_guards:
      enabled: true
      mode: strict
      max_deviation_bps: "200"
      resume_deviation_bps: "100"
      recovery_delay_seconds: "5"
      max_book_levels: 50
      max_own_orders: 200
  perpetual:
    quote_guards:
      enabled: true
      max_deviation_bps: "200"
      resume_deviation_bps: "100"
      recovery_delay_seconds: "5"
profiles:
  sideways:
    scenario: sideways
    anchor: session_start_mid
    sideways_range:
      lower_price: "0.09"
      upper_price: "0.11"
      resume_lower_price: "0.095"
      resume_upper_price: "0.105"
```

`abs(reference / qualified_market_mid - 1) * 10000` must be at or below the outer
deviation limit. Equality is allowed; 100 bps means 1%. Enabled guards require
explicit finite thresholds with `0 <= resume_deviation_bps < max_deviation_bps <
10000`. Recovery defaults to five seconds (allowed 0–3600). Inclusive manual outer
bounds must contain the tighter inclusive resume range and lie within absolute
price limits. A range on a non-sideways profile, shared market template or disabled
guard is rejected. `strict` never uses `initial_anchor` to bypass an empty/own-only market.
Explicit [listing bootstrap](#listing-bootstrap) adds a finite,
durably bound episode with the existing spending/drawdown protection.
The existing catalogue validates every profile against every configured market;
a manual-range profile therefore requires guards enabled on those market templates.

### Qualified market observation

The controller reuses connector books and active, recently terminal and lost
orders from the existing tracker. It reads at most `max_book_levels` levels per
side (default 50, allowed 1–200) and bounds inspected identities with
`max_own_orders` (default 200, allowed 1–1000). Other-pair identities also count
against inspection. Readiness/send checks make no full-book DataFrame, history
query or exchange request.

At matching prices, stable acknowledged unfilled orders contribute their known
remaining base quantity. Pending creates/cancels, partial fills, recent terminal
orders and private updates newer than the book contribute their **full original
amount as a conservative upper bound**. Only depth above the combined bound can
qualify. Smaller ambiguous depth is skipped; smaller stable acknowledged depth
is inconsistent. Thus private fills cannot turn a still-visible original own
amount into apparently external liquidity. Residual must exceed double rounding
noise and meet the venue's minimum base quantity. Perpetual WS snapshot quantities
use the existing contract-to-base conversion, matching REST snapshots and diffs.

Both usable sides, fresh non-crossed depth and consistent bounded observations
are required. Missing competitive acknowledged orders, insufficient filtered
depth or exceeding the identity limit yield `market_price_unavailable`. Enabled
freshness cannot exceed the existing 30-second terminal-order cache; the normal
default remains ten seconds. Recently filled/cancelled orders can conservatively
reduce qualified depth until that cache expires, delaying quotes on thin markets.

Public/private streams have no atomic common snapshot. Known-order filtering is
an observation, not proof of independent fair value. It cannot identify unknown/
manual orders or other accounts' orders, or prove that no exchange update is missing.

### Quote guard recovery and observations

An enabled guard starts waiting. At least two distinct public book observations
must pass the tighter boundary and the configured delay must elapse. Only the
controller's existing control loop samples recovery. Readiness/final-send checks
can revoke admission but cannot advance recovery or manufacture samples. Invalid
observations reset recovery; restart resets the delay while retaining session
anchors/deadlines. First admission captures `session_start_mid` from qualified
target depth. Repeated checks preserve the existing spread multiplier.

Breaches cancel quotes through the existing position-preserving path. In `both`,
either required market closes shared admission and both quote sets are cancelled.
The existing final transport gate rechecks after throttling; queued creates cannot
bypass a breach. Cancels remain allowed. Recovery cannot clear durable HALT,
renew expiry or restart an operator-stopped session. `/live` stays healthy while
the service progresses; `/ready` waits for trading admission.

`GET /api/v1/admin/status` adds optional `markets[].quote_guard` (null when disabled):

| Field/reason | Meaning |
|---|---|
| `ready`, `reason_code` | Admission and current waiting reason |
| `market_price_unavailable` | No qualified two-sided observation |
| `reference_deviation` | Reference exceeds its market-relative limit |
| `sideways_breakout` | Observed market is outside manual outer bounds |
| `guard_recovering` | Waiting for elapsed time and distinct tighter-bound observations |
| `data_quality`, `detail` | `known_order_filtered` / `unavailable`, plus bounded classification |
| `market_mid`, `deviation_bps` | Decimal strings when observed; null when unavailable |
| `book_observed_at`, `evaluated_at`, `recovery_started_at` | UTC timestamps, without claiming cross-stream synchronization |
| `recovery_valid_samples` | Capped at two; repeated reads cannot increase it |

Generate Swagger with `make swagger`; validate with `make swagger-check`.
Run the local simulator via `python scripts/test_e2e_offline.py --image
hummingbot-cloudrun:local --scenario quote-guards --trading-env demo`.
The short diagnostic fixture explicitly enables guards via `python
scripts/test_mm_load.py --image hummingbot-cloudrun:local --profile probe
--strict-guards`. Its thresholds are test configuration; this does not certify a
sixty-minute/all-enhancements release.

## Drawdown

The PostgreSQL MM service can use the existing V2 runner's drawdown thresholds.
Both limits are optional and disabled in the default catalogue. Configure them
at the top of `life_mm_profiles.yaml`, alongside `max_total_amount_quote`:

```yaml
# Examples for testing only; choose limits for the actual strategy and capital.
max_global_drawdown_quote: "10"
max_controller_drawdown_quote: "5"
```

Amounts are positive finite USDT values; `null` disables that limit before the
policy is first armed. At least one enabled limit activates managed drawdown.
Zero, negative, NaN and infinity are rejected. The supported market scope is the
catalogue's stable OKX spot/perpetual controller IDs and their USDT pairs.
The service generates the runner metadata; do not configure a second runner or
add an inactive trading controller to observe a retained position.

### Drawdown measurement and coverage

The metric is `covered_v2_report_drawdown`: peak covered bot trading-report PnL
minus current covered report PnL. It is **not account equity loss or session profit**.
The global report sums both configured controller identities, even while only
one market is selected. Each controller also has its own peak. Equality with a
limit is permitted; exceeding either limit stops new risk for the entire MM bot.

The initial peak has the existing runner's zero floor, and includes existing
recorded report history. A new session does not establish a new baseline. For
example, first observation `-2` means a drawdown of `2` from zero. An existing
historical loss may therefore breach a newly armed limit immediately. A later
PnL of `20` establishes a peak of `20`; a PnL of `9` has a drawdown of `11`.

The service reuses executor reports, streamed historical executor performance
and recorded `PositionHold` exposure. Active/cached executor net PnL already
includes their reported trade fees. Managed held-position reports subtract the
separately recorded quote-fee total once; holding an executor must not erase its
trade fees. Funding remains **excluded** and is reported by the existing funding
and daily APIs. Initial wallet inventory, transfers and manual/account-wide
trades do not become bot report PnL automatically.

Historical executor performance is grouped by the existing controller ID. Before
first arming, establish that those IDs belong to the intended bot history; this
phase does not add account/environment attribution or reinterpret old executor
configurations. Once armed, the stable controller/market policy cannot be changed
through ordinary trading commands.

This preserves the upstream valuation conventions: active executor PnL uses its
existing executable-side price/quantity calculations; held positions use the
current midpoint. Base-denominated fees and quantity rounding can produce a
difference during handoff. Historical executor PnL/fee columns retain their
existing Float precision; PostgreSQL held-position amounts retain Numeric
precision. This adapter does not claim an exact account ledger or add a second
PnL calculation engine. Select limits with these report semantics in mind.

### Drawdown waiting and restart

Report and risk-decision observations must be at most three seconds old. Required
marks must be positive, finite and within the most conservative configured market
data age. Both conditions are checked again at the existing final send gate.
Missing, errored or stale coverage is unavailable, never a fresh zero or a new
peak. Quotes are cancelled with filled inventory/positions retained; the process
and admin API remain available and admission resumes after valid observations
return. Session anchors and deadlines are unchanged.

After disabling a market with a retained nonzero position, the existing provider
keeps a public, non-trading order-book subscription for valuation. Read-only
initialization retries classified transient failures and is cancelled during
shutdown. No mode/leverage/order writes are needed for this observer. Unknown
recorded exposure outside the declared scope blocks admission and requires
operator review rather than silently omitting a position.

Confirmed breach uses the existing bounded, position-preserving service shutdown
and durable recovery HALT. It cancels partially filled remainders without placing
market closes. Normal manual-kill restart, profile switching, random steps,
spot/perpetual/both selection, stop/start and process restart cannot clear the
breach. Required checkpoint/persistence failures remain critical and cannot be
downgraded into optional reporting errors.

The existing `bot_state.recovery_state.strategy_checkpoints` stores policy,
stable scope, monotonic peaks and stop/breach state under `life_mm_profiles.yaml`.
No new table, migration, ledger, cache or reset endpoint is needed. Peak writes
occur only on changed state. Ownership fencing and command-version checks prevent
superseded startup from binding its old policy.

Once armed, the thresholds and scope are bound to that bot's existing checkpoint.
Normal MM commands can select profiles or markets under the same policy, but
cannot disable/change the limits, rename a scoped controller, change its pair or
switch to a legacy runner to bypass it. Policy changes require explicit reviewed
maintenance with the service stopped and exposure/history reconciled. Do not
delete checkpoints or clear HALT simply to resume quoting after a breach.

### Drawdown observations

`GET /api/v1/admin/status` adds optional `drawdown`; the existing authenticated
`/profiles` response exposes the catalogue limits. Swagger includes these DTOs.
Amounts serialize as decimal strings and observation times as UTC timestamps.
When disabled, `drawdown` is `null`. No new command or authentication route exists.

The status includes readiness, reason, scope, global/controller peaks, observed
PnL/drawdown, limits, `operator_action_required` and explicit fee/funding coverage.
Stopped or stale observations preserve known peaks and confirmed breach, while
current PnL/drawdown are `null`. Important reasons are:

| Reason | Meaning |
|---|---|
| `drawdown_report_unavailable` | Missing, errored or old report/risk observation; wait for valid coverage |
| `drawdown_valuation_unavailable` | Required active/retained position mark is unavailable or stale |
| `drawdown_position_scope_unavailable` | Recorded exposure is outside declared coverage; operator review |
| `drawdown_checkpoint_unavailable` | Policy not bound yet, or required checkpoint write failed; inspect service HALT/incidents |
| `drawdown_checkpoint_invalid` | Persisted peak/coverage cannot be validated; operator review |
| `drawdown_global_limit` | Confirmed global limit breach; durable HALT |
| `drawdown_controller_limit` | Confirmed controller limit breach; durable HALT |
| `drawdown_not_running` | Preserved checkpoint without a current trading observation |

Local verification commands:

```sh
make test-cloudrun-regression
make test-postgres
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:local --scenario drawdown --trading-env demo
python scripts/test_mm_load.py --image hummingbot-cloudrun:local --profile probe --drawdown --strict-guards
```

The probe explicitly enables test-only drawdown/guard thresholds. It does not
enable production defaults or replace the final E6 sixty-minute acceptance gate.

## Spot outflow

Managed PostgreSQL MM can limit rolling **spot trading cash consumption**. The
default catalogue has `spot_outflow: null`. Enable it with an explicit policy:

```yaml
# Test example only; select a window, cap and fee allowance for the actual bot.
spot_outflow:
  window_seconds: 3600
  max_net_outflow_quote: "100"
  fee_allowance_rate: "0.01"
```

The window is an integer from 1 to 604800 seconds. The cap is a finite positive
USDT amount. The explicit fee allowance is a finite fraction from 0 to 1.
The catalogue must retain a spot USDT market template even when perpetual-only
trading is selected. No environment variable or extra service is required.

### Outflow calculation

Use committed `trade_fills` for OKX spot and the bound pair in the UTC half-open
interval `[database_now - window, database_now)`. Net outflow is:

```text
BUY price × base amount - SELL price × base amount
+ actual USDT fees - actual USDT rebates
```

Original base/third-currency flat fees are reported separately, without an invented
USDT conversion. An original percentage without identified actual amounts is
unavailable for this metric until that observation leaves the window. Transfers,
perpetual fills, margin and funding are excluded. This is not wallet equity or
drawdown; the existing reporting APIs retain those separate observations.

For a proposed BUY, require:

```text
max(0, committed net outflow)
+ remaining reserved BUY limit notional and fee allowance
+ new BUY limit notional and fee allowance
<= cap
```

Pending sells do not fund buys. A known negative net outflow is floored at zero
before adding pending reservations. A BUY uses the greater protection of the
explicit percentage allowance and the validated existing fee estimate: an
estimated USDT rate above the configured allowance is rejected, and known
positive USDT fixed fees are additionally reserved. Unknown estimates block BUY.
The allowance is a conservative estimate, not a guarantee of future venue fees;
new admission closes if actual recorded charges exceed available capacity.

This is an admission constraint, not a guarantee that the observed rolling total
stays below the cap forever. Earlier SELL credits can leave the window before
later BUY debits, increasing the observed net without any new order or fill.
Existing fills cannot be undone; subsequent BUY admission checks retain the cap.
The economic stress replay reports these transitions explicitly. This policy is
also distinct from a gross-spending limit, account-equity loss limit or guarantee
of future fees.

Only existing post-only spot entries are supported under this policy. Perpetual
orders and funded spot sells remain subject to their ordinary account, balance,
exposure, session, recorder and ownership checks. A BUY-only cap/coverage denial
does not revoke the shared MM readiness gate or prevent cancellation. The LIFE
controller filters unavailable BUY proposals; the authoritative gates still run
before durable intent and immediately before REST transport after throttling.

### Outflow reservations and persistence

Reservations use the existing client order ID and quantized limit price/amount.
The same ID is counted once across repeated checks. A committed fill moves its
quantity from pending reservation into recorded cashflow. Tracker execution
quantity alone cannot release capacity before the recorder commit. A debit at
the exact upper window edge remains protected as pending risk until it enters
the window, including during reconstruction without a live reservation.

Local cancellation or failure alone is insufficient to free an unfilled remainder.
Read-only reconciliation reuses the existing connector's authenticated/throttled
order/fill APIs and fill parser. It verifies terminal identity/terms, paginated
fill completeness against cumulative quantity, and stable final order details.
Recovered fills use the existing recorder and primary-key deduplication; no fill
ledger is duplicated. Only proven terminal or durably rejected unsent remainder
is released. Incomplete reads/timeouts retain reservation and block new BUY;
funded sells and cancellation continue. The service observes these reads with
backoff. Shutdown uses its existing bounded position-preserving path.

The policy bounds admitted requests. It cannot retract a request already sent or
undo spending caused by a fill/fee update. A cap does not force market closes,
turn sells into taker orders, or clear recovery HALT.

### Outflow account binding

The existing `bot_state.recovery_state.spot_outflow` holds one immutable policy,
verified OKX account UID, demo/live environment, pair and coverage-start time.
Verification uses identity already observed by the normal connector. Both adapters
must identify the same account/environment when running together. Changed account,
environment or bound policy requires review; normal commands cannot disable the
policy, switch its pair or bypass it through a legacy runner. Profile changes,
random steps, market selection, clean restart and stop/start retain the window.

During reconstruction, startup retains account-setup authority while ordinary
order permission stays closed. It rechecks durable HALT after initialization;
finishing an observation cannot reopen orders after a binding failure.

An empty spot recorder and reconciled account can start immediately after binding
is committed. Existing unbound spot order history is handled conservatively:
BUY waits one full configured window from the verified binding/reconciliation
point. Old rows are not relabelled as activity of the current credentials. The
status exposes unknown cashflow as `null`, not a fresh zero. Funded sells can
continue under verified identity. The waiting deadline is durable across a clean
restart; unclean/uncertain recovery still retains the normal HALT.

Reconstruction streams at most 250001 rows from the existing market/pair/timestamp
index, with a 250000-fill in-process bound. Overflow is unavailable, never silently
truncated into a usable allowance. Reservations are bounded at 256 identities;
terminal reads handle at most four orders per ordinary observation pass and at
most 100 pages per order. Per-reservation deduplication retains at most 10000 fill
identities; exceeding that bound blocks BUY with explicit coverage failure instead
of growing indefinitely or forgetting prior commits. Known cashflow uses incremental post-commit updates and
heap-based expiry; status/proposals/reservation checks do not scan history or
query the exchange. Existing owner/recorder send-gate SQL remains authoritative.

This delivery assumes one bot/account history per DB. It does not add multi-account
attribution or an automatic policy/HALT reset. Reviewed maintenance must establish
history/exposure provenance before changing that binding.

### Outflow observations and verification

Authenticated `GET /api/v1/admin/profiles` includes the optional policy.
`GET /api/v1/admin/status` adds `spot_outflow`: policy/binding, used/reserved/
remaining quote amounts, original non-USDT fees, UTC window bounds, coverage,
readiness, pending terminal proofs and the last BUY denial. Money uses decimal
strings; observation time uses UTC `Z`; window fields are UTC epoch milliseconds.
`buy_allowed` describes this spending gate's coverage and positive capacity;
the proposed amount and every ordinary trading gate must still pass. Owner loss
marks the snapshot stale and hides current capacity. A cold stopped process shows
the binding with unknown current amounts until reconstruction.

Common reasons include `spot_outflow_legacy_window_uncovered`,
`spot_outflow_identity_unavailable`, `spot_outflow_binding_mismatch`,
`spot_outflow_fee_unavailable`, `spot_outflow_limit`, `spot_outflow_reconciling`
and `spot_outflow_projection_unavailable`. Identity mismatch retains durable HALT;
ordinary cap exhaustion/read-only observation waits do not terminate the service.

```sh
make test-cloudrun-regression
make test-postgres
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:local --scenario spot-outflow --trading-env demo
python scripts/test_mm_load.py --image hummingbot-cloudrun:local --profile probe --spot-outflow --drawdown --strict-guards
```

The probe binds an empty test DB before adding yesterday's synthetic history.
This establishes provenance without bypassing legacy warmup. Its explicit test
limits are not production defaults and the short probe does not replace E6.

## Listing bootstrap

`quote_guards.mode: bootstrap_then_market` lets a managed MM session quote a
fresh, tradable instrument whose book is empty or contains only known own orders.
It is opt-in; deployed defaults remain disabled and enabled guards default to
`strict`. It uses the existing LIFE controller, PositionExecutor, owner send gates,
recorder and PostgreSQL recovery row. No extra service, cache, subscription,
migration or environment variable is required.

### Bootstrap configuration

This partial example contains illustrative local test limits around 0.1 USDT.
Retain the existing market identities, balances, exposure/leverage limits and
ordinary quote allocation. Configure both market templates separately when using
`both`. Values below are not production risk defaults.

```yaml
max_global_drawdown_quote: "20"
spot_outflow:
  window_seconds: 300
  max_net_outflow_quote: "100"
  fee_allowance_rate: "0.01"
markets:
  spot:
    initial_anchor: "0.1"
    total_amount_quote: "100"
    min_price: "0.01"
    max_price: "1"
    quote_guards:
      enabled: true
      mode: bootstrap_then_market
      max_deviation_bps: "200"
      resume_deviation_bps: "100"
      recovery_delay_seconds: "1"
      max_book_levels: 50
      max_own_orders: 200
      bootstrap:
        duration_seconds: 30
        max_anchor_move_bps: "500"
        max_quote: "50"
        min_external_depth_quote: "100"
        max_market_spread_bps: "250"
        qualification_seconds: "2"
```

All six bootstrap fields are required, finite and positive. Duration is an integer
in 1–86400 seconds and cannot exceed the spot outflow window for a spot template.
Anchor move and spread are below 10000 bps; qualification is at most 3600 seconds.
`max_quote` is a total market quote allocation across BUY/SELL levels, not a
per-level or margin amount, and cannot exceed `total_amount_quote`. Perpetual
leverage does not multiply this bootstrap quote budget. Spot outflow separately
reserves BUY notional and the configured fee allowance; it remains BUY-only.

`initial_anchor` must be within the ordinary absolute bounds and any sideways
manual range. Global drawdown, spot outflow and the existing applicable exposure
limits are mandatory. Bootstrap cannot run through an unmanaged CLI controller.
Existing Basic Auth admin start/switch commands select spot, perpetual or both and
fixed/random profiles; no new bootstrap reset or re-arm endpoint is provided.

### Bootstrap clock and qualification

The existing session-admission transaction saves anchors and the first bootstrap
start/deadline together. A failed/superseded transaction admits nothing. Deadline
is the minimum of admission time plus duration and any finite session/step expiry;
an until-stopped session still has a finite bootstrap episode.
The immutable policy and episode are bound to bot/account/environment/instrument
in `recovery_state.listing_bootstrap`. Restart, new revision, random rotation,
manual profile or market-mode selection, stop/start and new session IDs preserve
that instrument's original clock and band. Ordinary commands cannot disable or
change an armed bootstrap policy. Changes require reviewed maintenance outside
this API; deleting checkpoints is not a supported trading operation.

Sideways uses the explicit anchor. Follow profiles reuse the existing weighted
return formula with that LIFE anchor and fresh benchmark inputs. Own quotes never
recapture the anchor. Reference basket anchors retain the existing per-step
persistence. Qualification changes validation, preserving a running step's saved
formula. All raw and actual quantized outer quote prices remain within the
bootstrap anchor band, absolute limits and applicable manual range. The existing
executor receives an internal configured-price opt-in for these post-only quotes;
ordinary maker price behavior remains the default. Crossing an observed opposite
quote waits/cancels rather than sending an avoidable post-only rejection.

Qualification uses known-order-filtered two-sided depth. Each side must meet
`min_external_depth_quote`; spread, freshness and ordinary deviation/range checks
remain relevant. At least two distinct controller book samples must meet the
qualification duration. Missing/insufficient/stale data or a sampling gap beyond
freshness resets the local dwell timer. Readiness, API polls and final-send calls
cannot advance it. A fresh usable market with a deviation/range violation blocks
quoting even before qualification. Reaching the bounded book-inspection limit does
not prove that no outside liquidity exists and does not permit bootstrap.

Qualification is committed before new authority is granted. It can complete after
bootstrap expiry while the existing session remains valid. Once committed, lost
outside liquidity moves to `waiting`; it never returns to bootstrap. Restart
preserves qualification but requires fresh local recovery/dwell observations.
In `both`, both selected markets must pass their policies before any order sends.
No fill is required or generated to establish qualification.

### Bootstrap admission and observations

The existing pre-intent and post-throttle owner gates check the binding, exact
order payload, actual price, current tracked original allocation and ordinary
session/health/HALT permission. Partially filled active orders retain their full
original bootstrap allocation. No second cash ledger is introduced. A queued
bootstrap order retains its captured deadline/band even if the market qualifies
while it waits. OKX `expTime` is bounded by that original deadline and existing
session/step deadlines. Cancellation retains the ordinary position-preserving path.

`/api/v1/admin/status` market `quote_guard.bootstrap` reports:

- `state`: `bootstrap`, `market_guarded` or `waiting`;
- `price_source`: `configured_anchor`, `reference_model` or `market_book`;
- immutable anchor/bounds/budget, UTC start/deadline and seconds remaining;
- persisted qualification, local dwell/sample progress, filtered depth and spread;
- whether a market price exists for breakout detection, plus the outer guard reason.

Configured/model prices are not independently established market fair value.
An empty book has unknown `market_mid`/outside price quality even while bounded
bootstrap quoting is permitted. Unfilled executors have known zero filled
notional/PnL; their absent close mark is reported as unknown, not invented. Filled
or held exposure still requires the existing fresh valuation/account coverage.

Known-order filtering has the same public/private stream and unknown-account
limitations as [strict quote guards](#quote-guards). Bootstrap supplies
bounded liquidity; it does not produce trades, volume or a chosen price chart.

## Inventory skew

Optional LIFE size adjustment reuses the original PMM
`calculate_bid_ask_ratios_from_base_asset_ratio` implementation directly. It keeps
the existing LIFE reference formula, PositionExecutor, post-only order type,
refresh/cancellation and position-preserving stop. No separate inventory engine,
market order/rebalance path, table, service, cache or environment setting is added.

### Inventory skew configuration

In the spot market template of `life_mm_profiles.yaml`:

```yaml
inventory_skew:
  enabled: false
  target_base_pct: "0.5"
  inventory_range_multiplier: "1"
```

Disabled is the default. Target is a finite fraction in 0–1: `0.5` means 50% of
observed base+USDT cash value in the base token. Range multiplier is finite and
positive. These names/defaults retain compatible upstream PMM meanings; enable
and review them for the account's inventory rather than treating defaults as a
production recommendation. Enabling on a perpetual template is rejected.
Spot and both market modes use the existing Basic Auth admin session commands;
perpetual retains its ordinary sizing and long/short/gross limits.

### Inventory skew calculation

Base ratio is `base_cash * outside_mid / (base_cash * outside_mid + USDT_cash)`.
Use the existing spot connector's original `cashBal` observations. Engine equity,
unrealized perpetual PnL and another connector's USDT are not added to this signal.
This is account cash inventory, not an isolated campaign wallet.

Outside mid reuses bounded known-order filtering, even if the strict quote guard
is disabled. Missing/own-only outside depth or a model/bootstrap anchor cannot
establish inventory valuation. A strict/bootstrap guard's available qualified
observation is reused directly. Session/reference anchors remain unchanged.

The original PMM calculation interpolates BUY/SELL multipliers in 0–2. Underweight
base increases BUY size and reduces SELL size; overweight does the reverse. A
saturated side can become zero and is skipped. LIFE derives the range from the
sum of **all unskewed level base quantities at their proposed prices**, multiplied
by `inventory_range_multiplier`. It does not import PMM V1's single base-denominated
`order_amount` assumption or replace LIFE with the generic PMM V1 order executor.

Only validated interpolation inputs/ratios use the upstream double interface.
Final sizing and cap arithmetic stay Decimal; nonrepresentable/nonfinite helper
inputs fall back to neutral. All level notionals are considered together and
scaled down if necessary to fit ordinary allocation (or the smaller active
bootstrap allocation). Existing connector quantization rounds down; zero/below-
minimum levels are skipped. Active/shutting-down executors reserve their full
original proposed allocation, including partial fills, before additional levels
are accepted. Rejected allocation is never redistributed to another side.

Ordinary spot outflow, available-balance and inventory caps then apply. These
remain independent risk checks. Perpetual/shared-collateral admission still uses
the existing joint readiness and minimum available USDT, rather than summing the
same wallet across adapters. The quote allocation is a proposal budget; it is
not a guarantee about execution proceeds, mark-to-market value or account equity.

### Inventory skew fallback

Missing optional cash fields, unusable outside valuation or helper failure uses
neutral multipliers `(1, 1)`, reports `inventory_skew_unavailable` and retries on
the next ordinary controller update. It does not restart a controller or process.
Missing/corrupt mandatory engine balances, unhealthy required providers or known
stale account observations still block new quotes with the existing readiness/
cancellation path. Recovery from fresh data is automatic.

Known account timestamps allow one second of future skew and at most two normal
connector long-poll periods (currently 240 seconds), rather than the ten-second
market-book timer. The existing OKX fallback polls quiet accounts every 120 seconds;
no new polling task/request is introduced. Missing optional observation metadata
alone selects neutral fallback while the ordinary engine checks remain in force.

`/api/v1/admin/status` exposes `markets[].inventory_skew`: active/neutral state,
reason, account-cash scope, cash balances, outside valuation/base ratio, proposed
multipliers, budget scale and UTC observation/evaluation times. Disabled and
perpetual policies return `null`. Status reads use the existing cached service
snapshot; FE polling cannot advance interpolation or call an exchange.

The upstream double interpolation may differ in insignificant low-order ratio
digits; final quantization/caps remain authoritative. Public/private books and
cash messages are observations from separate streams, not an atomic account
snapshot. This feature does not certify inventory profitability or ensure fills.

## Fee and volatility spreads

Fixed `buy_spreads` / `sell_spreads` remain the default. Configure each market
independently; spot and perpetual use their own existing candle feed and maker
fee helper. The three reference formulas and session anchors are unchanged.

The existing engine supports weighted multiple levels without a new strategy.
For example, three levels can use `buy_spreads: [0.004, 0.009, 0.018]`, the same
`sell_spreads`, and `buy_amounts_pct: [50, 30, 20]` /
`sell_amounts_pct: [50, 30, 20]`. These symmetric weights assign half the market
budget to each side, then 50/30/20 within that side. This is a sizing example,
not a production spread recommendation; minimum sizes and other guards can
prevent some levels from being placed.

To preserve base spreads with the current policy, set `min_multiplier: "1"`
or greater and make every explicit fallback spread at least as wide as the
corresponding base level. A multiplier of `0.5` intentionally allows narrower
spreads. Reduced sizing and extreme-volatility pause are separate opt-in
settings described below; these floor settings alone do not enable them.

```yaml
spread_policy:
  fee_floor: true
  volatility:
    enabled: true
    interval: 1m
    length: 14
    target_natr: "0.01"
    min_multiplier: "0.5"
    max_multiplier: "3"
    max_age_seconds: 120
    on_unavailable: static
    fallback_buy_spreads: ["0.01", "0.02"]
    fallback_sell_spreads: ["0.01", "0.02"]
```

This example is a configuration example, not a production risk recommendation.
Fallback lists must explicitly cover all existing levels, remain ordered and
contain fractions between zero and one. `on_unavailable: wait` needs no fallback
lists and prevents new quotes until enough fresh completed candles return.
Missing candles do not terminate the service. In both-market sessions, existing
joint readiness still waits if either market uses the wait policy. API/control
remain available while their required dependencies are healthy.

NATR reuses the upstream PMM dynamic indicator without its MACD price shift.
The multiplier is `NATR / target_natr`, clamped to the configured limits. Zero
volatility selects the lower limit. The forming candle is excluded; freshness
is measured since the last completed interval ended. Non-finite, insufficient,
reordered/duplicate or stale completed data selects the declared fallback/wait
policy, recovering on the same controller. After a gap, the indicator uses the
newest contiguous suffix once it has `length + 1` completed candles; disconnected
older history does not force another hundred intervals of waiting. A bounded candle window is inspected
on control samples; NATR is cached per completed interval. Send checks do not
construct DataFrames or fetch candles. Feeds share existing connectors/throttlers
and controller ownership; the last managed user releases its feed. Unmanaged
upstream feeds retain their original readiness/lifecycle.

An enabled fee floor applies after dynamic or fallback spreads. It values the
connector's estimated maker fee in quote currency through its existing helper.
Perpetual fee estimation supplies the existing required `PositionAction.OPEN`
argument. Base/quote percentage and flat fees are supported; an unknown third currency
waits with `fee_basis_unavailable`, without assuming zero or querying an oracle.
Rebates cannot narrow the configured spread; net negative estimated fees are
clamped to zero. Each proposed leg must cover its estimated fee relative to the
model anchor. This covers a hypothetical maker buy/sell round trip around that
anchor; it does not guarantee a matched round trip, realized profit or protection
from adverse selection/inventory repricing. Actual fees remain in recorder history.

Enabled policies use the existing executor configured-entry-price option. BUY
prices round down; SELL prices round up to preserve the composed spread. If SELL
rounding increases price, quantity scales down so quote allocation cannot grow.
Existing amount rounding, funding, exposure, bootstrap bands, drawdown, outflow,
post-only and ownership gates still apply. Managed PostgreSQL sessions recheck actual tracked terms and fee coverage
before intent and immediately before transport. A queued/stale or
rounded quote that no longer satisfies the policy is rejected; cancellations
remain available. A tiny Decimal numerical guard is below exchange tick precision
and is not a configurable economic buffer.

`GET /api/v1/admin/status` exposes typed `markets[].spread_policy`: current
static/dynamic/fallback/wait state, cached multiplier, completed interval/freshness
and effective level spreads. Decimal values are strings and timestamps are UTC.
Guard history records state changes without storing each candle or quote tick.
No new service, task, database table, external cache or environment flag is added.

### Volatility defense

Managed PostgreSQL sessions can opt into `spread_policy.volatility.defense`.
Absence/null preserves the previous behavior. Existing quote guards, volatility
and maker fee floor must be enabled. `min_multiplier` must be at least one and
fallback lists must be no narrower than their matching base spreads. No API
command can override the YAML policy or reset a saved pause.

Disabled defense is omitted from serialized policy snapshots to preserve the
legacy shape. Once a session or saved pause uses defense, its serving and
rollback revisions must understand this policy/checkpoint schema. Verify a
compatible prior artifact before a rollout; an older unsupported runtime is
not a recovery method for an active pause.

Append this fragment **inside an enabled volatility configuration**, together
with explicit floor/fallback/guard settings. Values illustrate the schema only;
they are not calibrated LIFE listing thresholds:

```yaml
defense:
  reduce_at_natr: "0.02"
  stop_at_natr: "0.05"
  resume_below_natr: "0.015"
  reduced_size_factor: "0.5"
  recovery_seconds: 60
```

Thresholds use raw completed-candle NATR fractions, before spread-multiplier
clamping. At/above the reduction threshold, the market/bootstrap allocation is
scaled down before weighted sizing and inventory skew. The same reduced budget
still bounds skewed proposals; rejected/minimum-size levels are not enlarged or
redistributed. Fees, balances, exposure, outflow and final exchange rounding
remain authoritative. Oversized or newly too-narrow working quotes are cancelled
through the existing position-preserving executor lifecycle. Pending original
allocation stays reserved until terminal proof; replacements cannot stack over
an unconfirmed cancellation.

Cold starts and profile transitions also begin at reduced size until fresh data
qualifies normal sizing. Recreating a controller therefore cannot jump past the
resume threshold inside the hysteresis band. Smaller orders can remain active
during this qualification; no additional durable reduction record is needed.

At/above the stop threshold, new quotes pause and working remainders are
cancelled. The PostgreSQL owner checkpoints a small account/environment and
connector/pair pause latch. Stop/Start, random steps, switching market/profile
and restart do not clear it. Recovery requires fresh completed candles below
the reduction threshold, at least two distinct increasing candle identities and
the elapsed monotonic recovery duration. It resumes reduced; another qualified
window below `resume_below_natr` permits normal sizing. Offline time and repeated
API/send checks earn no recovery credit. A paused policy cannot be removed or
changed on session admission until it recovers under its saved policy; restore
the YAML or arrange operator-reviewed maintenance if necessary.

Automatic random rotation keeps the recovering controller while a known pause
or its pending checkpoint remains active, then skips elapsed windows after
recovery. The old step's send authority still expires on schedule; overall timed
expiry and explicit Stop/Switch still apply. This prevents short random steps
from repeatedly erasing recovery samples. Recording a newly observed breach may
survive a racing Stop/Switch, but clearing the latch requires the exact current
control version and never clears HALT.

Missing candles use the declared `wait` or explicit `static` policy. Static
fallback uses reduced size and still requires trustworthy mandatory price data;
it cannot clear an existing extreme-volatility pause. No candles at initial
listing is not proof of extreme volatility. Strict guards still require outside
liquidity, and bootstrap still needs its explicit bounded permission and original
finite deadline. NATR is not an instantaneous price-jump detector: existing
current-book/deviation checks remain active.

The service/API stay alive during temporary waits. In `both`, existing joint
readiness pauses both markets when either requires a wait. Fresh-data recovery
cannot restart an operator-stopped/expired session or clear risk HALT, ownership
loss, uncertain exchange effects or a failed durable checkpoint. Unsupported
unmanaged/file-mode defense is rejected instead of pretending to persist it.

`status.markets[].spread_policy` adds fresh `raw_natr` and optional `defense`:
state, English label/next action, size factor/effective allocation, evaluated
time, sampled recovery progress, saved-latch status and pending cancellation.
Cancellation pending is not exchange terminal proof; current working orders
remain visible separately. WARN/INFO transition logs and existing guard history
record restrictions/recovery, without per-tick candle storage or FE-triggered
exchange reads. See [FE integration](fe-integration.md) and the
[implementation checklist](plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md).
