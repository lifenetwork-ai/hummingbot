# Pre-listing economic stress replay

This experiment compares the existing LIFE controller under **synthetic,
uncalibrated assumptions**. It does not establish LIFE fair value, expected
returns, real queue priority, market impact or an optimal production strategy.
All sample monetary values, fees and funding are experiment inputs.

```sh
make test-mm-economics-probe  # 108 diagnostic cases
make test-mm-economics       # Full declared 648-case matrix
python scripts/test_mm_economics.py --config test/e2e/offline/economics.yaml
```

`--volatility-defense` adds a fourth explicit comparison variant (fee floor,
volatility, spot skew and defense), leaving the original three variants intact.
With the default YAML this is 864 cases, or 144 for `--probe`. A smaller declared
YAML may be used for a scoped comparison; report that scope separately from the
default matrix. Defense thresholds are synthetic assumptions retained in each
effective controller configuration, not production calibration.

`metrics.defense_seconds` counts simulated state time separately for each market;
do not add both markets and call the result elapsed session time. The adapter
uses the real controller's reduced sizing, cancellation proposals and sampled
recovery. Its checkpoint callback is a local boundary shim: PostgreSQL persistence,
owner fencing and real send/cancel behavior require the separate integration/image
tests. No replay result establishes improved expected profit.

Use the compiled Python environment. No local `.env`, exchange credentials,
network connector or database is loaded. Reports go to a unique directory under
ignored `build/mm-economics/`. `--output <new-directory>` selects another location;
an existing output directory is rejected. `--probe` is explicitly diagnostic.

The YAML declares six scenarios, two seeds, three fixed profiles, spot/perpetual/
both, three policy variants and two assumed fee/latency/queue conditions. The
reference basket and single-token reference are configurable within the generated
BTC/ETH/AVAX tape. Random profile optimization is outside this first experiment.

## Reuse and boundaries

The adapter reuses the deployed `MMProfilesConfig`, `LifeMMConfig` and actual
`LifeMMController` for reference prices, allocation, exposure, refresh, strict
guards, finite listing bootstrap, fee floor, NATR and spot inventory skew.
`PositionExecutor.entry_price` supplies the original maker price convention.
`SpotOutflowProjection` reserves pending BUY spending and accounts for fills.
`BacktestPositionHold` independently retains HEDGE legs. The existing simulator
supplies the order/fill/fee/balance ledger; its amounts are cross-checked using
the existing recorder-shaped ledger validator. All replay prices/amounts use
Decimal except the existing book/indicator numerical interfaces.

This is a controller-level adapter, not a full executor/connector/DB replay.
Ownership, recovery, durability, real account setup, private-feed delay and
covered-V2 drawdown stopping retain their separate E2E evidence. The experiment
does not replace those tests or run the production owner/transport gates.
Bootstrap checkpoint transitions occur in memory; they are not DB transactions.

## Conservative execution assumptions

- Exogenous tapes are generated before strategy decisions. Every policy, profile,
  market mode and sensitivity for a scenario/seed receives the same tape. LIFE
  can move independently of BTC/ETH/AVAX. No bot action changes future prices/flow.
- Synthetic books contain one outside level per side. Its depth replenishes at
  each one-second observation; the YAML declares depth and spread. Visible better
  liquidity consumes aggressor quantity before the bot. Unknown same-price queue
  volume is an additional conservative assumption, without queue-cancellation credit.
- Touching a quote does not prove a fill. A flow must trade through it; quantity
  is shared in price/time order. Partial caps and lot rounding apply. Unconsumed
  flow after a partial best-price allocation is discarded, never reused deeper.
- Placement and cancellation delays are distinct. Arrival checks post-only against
  outside quotes and prior own orders; no own-order self-match is generated.
  A create arriving after its captured bootstrap/session deadline is rejected.
  Already accepted quotes can still fill until cancellation acknowledgment.
- Current market changes/fills happen before the controller reacts. This retains
  adverse gap/cancel exposure rather than using future prices to cancel first.
- Candles contain only observations already received, with no invented pre-listing
  candle history. The real spread helper uses its declared static fallback until
  sufficient completed synthetic one-second candles exist.
- Quotes stop at the finite session deadline. A common cancellation tail covers
  pending acknowledgments; all tail fills/costs remain in accounting. Inventory
  remains marked at the final scenario price and is not assumed liquidated.

The model assumes known immediate private fill/account observations, HEDGE OPEN
positions, USDT-only fixed initial margin and no borrowing. Funding settles on
an explicitly accelerated synthetic schedule. It has no maintenance-margin,
liquidation or dynamic exchange price-band model. Negative initial-margin
headroom is reported as a model/capacity concern, not a successful real position.

Fees/rebates are assumed paid in USDT; base/third-currency fee schedules are not
modeled here. The 648 parameter combinations reuse twelve scenario/seed groups
with eleven distinct tapes (empty/no-flow tapes are identical across seeds),
so they are not 648 independent market observations. Positive-case counts are
descriptive outcomes, not an estimated probability of making money.

## Metrics and evidence

`integrity_passed` means the entire declared experiment completed and its ledger,
flow, spending and source-consistency assertions passed. It does **not** mean the
strategy is profitable, meets liquidity targets or is approved for production.

Final account value includes cash, marked spot LIFE and perpetual unrealized PnL;
trade fees and signed funding are included once. Hold-relative excess compares
the identical initial LIFE/USDT wallet without trading. Illustrative external
operating cost is subtracted separately; it is not a Cloud Run price estimate.
Per-market contributions exclude the initial wallet's passive holding return
and sum to shared-wallet excess before operating cost. Cash/collateral is never
duplicated between spot and perpetual. Inventory skew applies only to spot.

Drawdown measures full synthetic wallet equity, including its initial LIFE
inventory. It is distinct from the covered-V2 drawdown guard. Remaining inventory
and positions, gross exposure and initial-margin headroom are explicit. Marked
PnL has no assumed terminal sale, exit slippage or exit fees.

The spot rolling outflow limit constrains **new BUY admission**, using the existing
net committed-flow formula and reservations. Expiring older SELL credits can
raise the measured net above the cap after previously valid buys; it is not a
continuous hard loss/gross-spending guarantee. The experiment retains every BUY
admission check, peak rolling measurement and above-cap observation rather than
clipping results or treating that legitimate window transition as corrupt data.

Liquidity requires both resting sides around the independent scenario mark,
the declared maximum spread and minimum depth within its half-band. The full
session is the denominator, including startup, waits and bad quotes. Both mode
also measures simultaneous quality, not the average of alternating market uptime.
Empty-market eligible coverage and zero-volume fee ratios are null, never 100%
or zero-cost success. Signed 1/5/30-second markouts censor missing tail data.

`result.json` retains assumptions, source hashes, per-case summaries and descriptive
groups. `review.md` is readable output, `cases.jsonl` persists completed results,
numbered case files retain effective configs/orders/fills/equity/funding, and
deduplicated tapes retain prices/references/aggressor flow. Failure preserves
completed cases and leaves integrity false. Do not interpret averages across
different stress regimes as a fitted profit estimate or rank a global winner.

`exogenous_tape_count` in the raw result counts paired scenario/seed groups;
content-addressed tape files are deduplicated by SHA-256 and can be fewer.
