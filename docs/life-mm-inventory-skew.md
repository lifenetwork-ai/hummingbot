# Spot inventory skew

Optional LIFE size adjustment reuses the original PMM
`calculate_bid_ask_ratios_from_base_asset_ratio` implementation directly. It keeps
the existing LIFE reference formula, PositionExecutor, post-only order type,
refresh/cancellation and position-preserving stop. No separate inventory engine,
market order/rebalance path, table, service, cache or environment setting is added.

## Configuration

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

## Calculation and bounds

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

## Fallback and observation

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
