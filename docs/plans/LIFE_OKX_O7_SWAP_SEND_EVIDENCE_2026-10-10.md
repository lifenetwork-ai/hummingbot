# O.7.1 — Protected SWAP Send Evidence

Date: 2026-10-10. Scope: the isolated LIFE-USDT-SWAP protected executor/send boundary.

**O.7.1 is complete offline. O.7 remains open.** This report does not certify a complete perpetual market maker, shared capital allocation, hedge execution, SWAP recovery or P7 inventory execution. Production permission remains disabled.

## Implementation and TDD

1. Red: protected SWAP connector tests failed because the persisted-ID scheduler and protected-pair callbacks were absent. Executor tests then failed at collection because the SWAP sender did not exist.
2. The first connector implementation exposed an additional fault: its `_api_request()` override discarded the final-send callback. A test waiting inside the actual REST throttler showed that revocation could otherwise miss the HTTP boundary. Forwarding the callback through that override fixes the path.
3. Green: `ProtectedSwapExecutorSender` durably registers immutable configurations/permits and instrument/side/level WAL claims before scheduling. `LifeLiquidityController` routes explicitly issued SWAP configurations through the existing OrderExecutor protected-submit hook. Ordinary unowned SWAP configurations receive no permission.
4. Fault tests cover late changes, ambiguous outcomes, restart, partial setup writes, pending close orders and malformed success responses. Synthetic account values and an explicit reservation-authority callback isolate this contract from the still-open financial coordinator.

## Verified contracts

| Contract | Offline evidence |
|---|---|
| Actual runner/send interfaces | The real `StrategyV2Base` action filter and `OrderExecutor.place_open_order()` reach the real perpetual connector scheduler, `_create_order()`, `_place_order()`, REST assistant and throttler. Only the HTTP session is fake. Revocation during throttling sends zero HTTP requests; success sends one. |
| Account and order identity | Explicit UID/account mode, configured cross margin, ONEWAY/HEDGE, leverage, fresh finite positions, connector mode/leverage/`ctVal`, exact base-to-contract conversion and tick/lot limits are checked. Immutable configuration and journal identity prevent substitution. |
| Typed capital contract | The external authority receives session/config/risk epochs, account UID, instrument, direction, OPEN/CLOSE, contracts, LIFE quantity, contract value, leverage and price. A strict `True` approval is required again at final send. This interface is not an implementation of shared capital reservations. |
| Close bounds | ONEWAY CLOSE carries `reduceOnly=true`; HEDGE CLOSE binds BUY to short and SELL to long. Position shrink and other local/external pending close quantities can revoke the order before send. External and local pending sets are conservatively added until authenticated identity can establish their overlap. |
| Late revocation | Session expiry, clock rollback, risk epoch/approval, UID, observation freshness, connector mode/metadata, margin/leverage, runtime HALT/stale/missing safety journal, invalid config update, stop and invalid runner scope block the final request. |
| Durable uncertainty | WAL reaches `SEND_UNKNOWN` before the scheduler call. ACK changes it to `ACKED`, never terminal. Transport uncertainty, failed action checkpoints and final-send rejection retain claims. An ACK without a completed final authorization is rejected. Missing/empty/non-string exchange IDs cannot become false ACKs. |
| Restart and retry | Restore preserves unresolved wire IDs without restoring send authority. An unknown old claim prevents new proposals; partial executor retries cannot silently replace the original quantity. A final callback cannot be reused. |
| Request budget | The account-bound budget is charged at final send. When spot already consumed the available non-cancel capacity, SWAP creation is denied and cancel capacity remains available. Installation requires the same budget object when a spot request budget is installed. |
| Storage and ownership | Missing, changed, uncertain or stale WAL/action journals fail closed. Orphan WAL claims from a failed action checkpoint prevent further proposals. Spot and SWAP cannot reuse a spot-owned intent ID; separate SWAP WAL state does not pretend to match the spot reservation ledger. |

Tests: [test_protected_swap_send.py](../../test/hummingbot/strategy_v2/life_liquidity/test_protected_swap_send.py) and [test_swap_executor.py](../../test/hummingbot/strategy_v2/life_liquidity/test_swap_executor.py). Paths are relative to the repository root when running the commands below.

## Verification

Focused run:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_protected_swap_send.py \
  test/hummingbot/strategy_v2/life_liquidity/test_swap_executor.py
```

Result: **63 passed**, 6 existing dependency warnings. No credentials, network requests or exchange orders were used.

Broader regression: **1,610 passed, zero skips/failures**, 27 existing warnings. Changed-line coverage is **89.48%** against the pinned feature baseline and **91.62%** against the pinned O.2 baseline; all 60 changed Python production files are measured. The new protected SWAP module has **94.62%** changed-line coverage. Repository pre-commit checks pass.

Full counts, baselines, source hashes and coverage are recorded in [the regression artifact](evidence/life_o7_swap_send_regression.json). Reproduce its baseline acceptance run with:

```sh
conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o7-swap-acceptance
```

The audit still certifies only the **spot O.6 baseline** and its explicitly classified deferrals, while executing the additional SWAP tests. Its scope label does not claim Full Offline Complete. The archived artifact includes production source hashes and the focused test node count for this SWAP slice.

## Remaining O.7 work

- O.7.2: one durable, atomic spot/perpetual/MM/hedge capital authority with collateral tiers, gross/net pending exposure and stress limits.
- O.7.3: actual hedge generation/coordinator, authenticated-style fill accounting, partial fills, bounded retries, executable costs and urgent exposure deadlines.
- O.7.4: distinct mark/index/funding/fee adapters, dynamic funding settlement, basis stress and monitoring after quote-session expiry.
- O.7.5: account-scoped SWAP cancel/order/fill reconciliation and cold restart; release claims only from evidence. Cross-product session successors must wait for both products. The current sender conservatively holds all uncertain claims and cannot recover them automatically.
- O.7.6: protected P7 inventory children, terminal claim release, fill/fee/shortfall attribution, hard participation/impact/deadline constraints and MM/inventory transitions.
- O.7.7: combined extension replay, A-case/requirement evidence, regression and coverage closure.

Actual account qualification, deployed ownership, market/fee observations and demo/live release remain D/R/C. The synthetic account mode, TTL, balances, quantities and request limits here are test fixtures, not live defaults.
