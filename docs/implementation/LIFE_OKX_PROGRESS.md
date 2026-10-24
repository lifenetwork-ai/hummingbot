# LIFE / OKX implementation progress

Updated: 2026-10-25 (Asia/Ho_Chi_Minh).
Delivery checklist: [current plan](../plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).

## P1 — Controller and configuration foundation

Implemented locally. The service/session integration is P2; admin endpoints are
P3. No exchange account, external order or deployment was used for this phase.

### Reuse map

| Existing component | Gap and smallest extension |
|---|---|
| `PMMSimpleConfig` / `PMMSimpleController` | Subclass as `LifeMMConfig` / `LifeMMController`; retain V2 level sizing and PositionExecutor actions, add weighted/fixed references and explicit MM policy |
| YAML loader and Pydantic models | Package `life_mm_profiles.yaml`; validate profiles, durations, market templates, instrument membership and combined declared budgets through `MMProfilesConfig` |
| V2 controller discovery and periodic reload | Existing loader discovers `life_mm`; API-managed effective configs resist periodic YAML replacement; ordinary updates still use the original validated path |
| `MarketDataProvider` dynamic books | Wrap existing initialize/remove operations with consumer tracking, retain shared/preexisting target feeds and propagate initial-book timeout correctly |
| Existing OKX position conversion metadata | Convert Decimal contract count with `_contract_sizes` for REST and WS; remove dependence on notional/entry price and integer net-position parsing |
| OKX spot instrument filter | Require live SPOT metadata; keep malformed-live-rule regression meaningful by supplying `state: live` in that fixture |
| PositionExecutor refresh/hold | Use post-only entries, disable automatic rebalancing/exit barriers, refresh with `keep_position=True`; reuse the existing held-order helper and retain cancelled partial fills |

Runtime controller checks include fresh source/target books, quote bounds, actual
spot balances, separate long/short/gross perpetual exposure, pending executor
entries and available funds for a batch. Partially filled pending entries can be
counted conservatively until their executor enters trading state. Held exposure
is read from actual connector balances/positions rather than added twice.
Perpetual setup requires confirmed HEDGE mode and a successful CROSS leverage
request; account-mode changes with existing tracked orders/positions are rejected.

An explicit `initial_anchor` is a fallback for a fresh empty/one-sided listing
book. A usable midpoint takes precedence. It cannot bypass stale book data or
replace a missing benchmark source. Once captured, controller anchors remain
fixed; durable session capture/recovery belongs to P2.

The YAML spot template exposes its inventory cap; the perpetual template exposes
long/short/gross caps and CROSS/HEDGE settings. Values are examples requiring
instrument/budget review before use. Loading the catalogue does not start trading.

### Red evidence

Meaningful failures were run before the corresponding implementation/fix:

- `test_position_quantity.py`: 12 failures for wrong `0.6 -> 1` rounding,
  zero/empty entry price, fractional signed net contracts, zero-position removal
  and absent contract metadata. An initial fixture event-loop issue was corrected
  before counting this Red run.
- Initial `test_life_mm.py`: 16 failures / 1 pass for live-price drift instead of
  saved references, invalid profile acceptance and non-post-only entry defaults.
- `test_mm_profiles.py`: missing deployment catalogue assertion first; then six
  failures for the missing selection/override/budget behavior.
- `test_reference_subscriptions.py`: three failures for duplicate subscriptions,
  premature shared/target removal and non-idempotent release; a separate failing
  timeout assertion exposed the existing false-success startup return.
- Instrument eligibility: five failures for suspended/preopen/unknown spot state.
- PositionExecutor: cancelled `0.4` partial fill incorrectly became `EARLY_STOP`
  without held exposure; a real `InFlightOrder` and `TradeUpdate` reproduce it.
- Additional controller Reds covered inventory/pending/gross caps, stale-basket
  handling, filled-position refresh, account setup, empty listing fallback,
  cancellation on quote bounds and midpoint precedence over the fallback anchor.

### Green evidence

Python: `/opt/homebrew/Caskroom/miniforge/base/envs/hummingbot/bin/python`.

Focused new suites: **68 passed**. Broader affected suite: **510 passed**:

```sh
python -m pytest -q --disable-warnings --tb=short \
  test/hummingbot/strategy_v2/controllers \
  test/hummingbot/strategy_v2/executors/position_executor \
  test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py \
  test/hummingbot/data_feed/test_market_data_provider.py \
  test/hummingbot/client/config/test_mm_profiles.py \
  test/hummingbot/connector/derivative/okx_perpetual \
  test/hummingbot/connector/exchange/okx \
  test/hummingbot/strategy/test_strategy_v2_base.py
```

Repository pre-commit checks pass. The broad suite reports existing deprecation
warnings and an unawaited AsyncMock warning; the focused new suites have no
RuntimeWarning. These results cover unit/regression behavior, not image, load or
exchange acceptance.

## Next — P2

Persist the current session, effective basket, anchors, deadlines and random draws
through the existing PostgreSQL owner/control machinery. Connect the catalogue
to the existing V2 service lifecycle; admission must use observed collateral,
joint readiness for `both`, recovery/HALT and final-send checks. Session clocks,
API-managed selection and anchors must survive restart without extending time.
Real PostgreSQL CAS/replay/recovery evidence is required before completing P2.
