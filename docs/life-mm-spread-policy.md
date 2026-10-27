# Optional fee and volatility spreads

Fixed `buy_spreads` / `sell_spreads` remain the default. Configure each market
independently; spot and perpetual use their own existing candle feed and maker
fee helper. The three reference formulas and session anchors are unchanged.

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
