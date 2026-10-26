# Bounded listing bootstrap

`quote_guards.mode: bootstrap_then_market` lets a managed MM session quote a
fresh, tradable instrument whose book is empty or contains only known own orders.
It is opt-in; deployed defaults remain disabled and enabled guards default to
`strict`. It uses the existing LIFE controller, PositionExecutor, owner send gates,
recorder and PostgreSQL recovery row. No extra service, cache, subscription,
migration or environment variable is required.

## Explicit configuration

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

## Clock, price and qualification

The existing session-admission transaction saves anchors and the first bootstrap
start/deadline together. A failed/superseded transaction admits nothing. Deadline
is the minimum of admission time plus duration and current session/step expiry.
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

## Send gates and UI

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
limitations as [strict quote guards](life-mm-quote-guards.md). Bootstrap supplies
bounded liquidity; it does not produce trades, volume or a chosen price chart.
