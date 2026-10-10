# O.7.3 — Hedge execution coordinator

Date: 2026-10-10. **O.7.3 is complete offline for this contract. Full O.7 remains open; production trading remains disabled.**

## Implemented contract

`HedgeCoordinator` is explicitly installed after the protected SWAP sender and shared capital authority. It owns hedge generation and blocks bypassing SWAP actions. The controller's action loop polls supplied settlement evidence, emits cancellation/hedge actions and admits normal spot planning only while the coordinator allows it. Protected spot and SWAP senders revalidate it independently at final send.

The coordinator uses the existing O.7.2 scope: LIFE-USDT spot, linear LIFE-USDT-SWAP, account mode 2, cross margin and USDT-valued collateral. ONEWAY and HEDGE modes close the correct opposing position first; one child never crosses a flat position. Contract metadata supplies `ctVal`, tick, lot and minimum size. Quantities round down. The generic example now accepts an explicit spot pair instead of constructing `asset-USDC`; existing SOL-USDC is its explicit default. Configurations changing the asset must also specify a matching pair. LIFE uses the protected route, not the generic example's market orders.

Actual spot balances come from the durable reservation ledger; its balances, reservations, trades and account events must match disk. The shared capital snapshot and SWAP account observation must agree on UID, modes, sequence, freshness and separate long/short positions. Existing spot WAL files must match disk. Pending spot orders are cancelled/reconciled before a hedge is issued and checked again at final send. Expected hedge fills never offset actual exposure.

Residual LIFE exposure is `actual spot + actual long − actual short − configured target`. Target updates have an idempotency ID and expected version. An identical repeated update returns without applying again, including after restart; conflicting IDs, stale versions and changes during a pending hedge are rejected. Fills change actual holdings; they do not silently rewrite the configured target.

## Execution, economics and urgency

- One durable child owns immutable executor configuration, original positions, mark, filled quantity, trade identities and cost hold before the protected sender allocates capital or publishes its WAL/action.
- Every child uses `LIMIT_MAKER`, the shared capital journal and the actual V2/OrderExecutor/OKX protected path. An issuance/checkpoint failure keeps ownership unresolved and cannot grant retry authority.
- Ordered, non-crossed depth gives a size-aware executable VWAP. The child itself quotes at the maker-side top. Cost reserves adverse mark-to-executable movement, absolute mark/index divergence, fees using the larger of the qualified fee and configured floor, and explicit funding cost. VWAP already incorporates depth impact; it is not added a second time.
- Cost must fit the per-child cap, explicit available-edge observation and remaining campaign cost budget. The campaign cost hold remains charged across cancellations, target changes and balanced episodes; rebates and unfilled orders do not refund it in this phase.
- Final authorization rechecks the current actual residual, order ownership, maker price, fresh sequence-bound depth/fees/funding and held cost after the real REST throttler. Cost growth, stale evidence or changed direction denies HTTP. Unsafe working hedges receive stop actions; their capital holds remain.
- Qualified market sequences checkpoint even when the financial gate refuses the order, preventing an older cheaper snapshot from restoring permission. Actual unhedged age checkpoints before market validation, so a data gap cannot restart the deadline.
- Deadband and batch preferences reduce churn. A durable timer and maximum unhedged quantity override those preferences. The explicitly selected `bounded_maker_and_pause` policy cancels/pauses spot quotes and attempts only a permitted bounded maker hedge. It does not guarantee execution by the deadline. Subminimum dust, costly books, insufficient capital, disconnect or session expiry keep quoting paused; there is no taker fallback or risk-limit relaxation.

## Partial fills and retry authority

The supplied settlement contract includes account UID, intent/wire/exchange identities, matching account snapshot sequence, cumulative base quantity and the full trade set. Each trade has an immutable ID, contract-consistent quantity, a limit-respecting fill price and nonnegative conservative quote fee. Duplicate/conflicting trades, omitted prior fills, inconsistent cumulative quantity or positions, and overfills fail closed.

Partial fills update a durable fill set but leave the child pending. ACK, timeout, cancellation request and missing settlement do not complete it. A complete terminal proof must agree with the already reconciled terminal WAL before a residual replacement is possible. Persistent attempts cap each continuous unbalanced episode; only an observed balanced state with every child terminal resets that episode. Realized adverse fill costs above the reserved cost, or invalid reconciliation evidence, latch a durable fault and cancel/pause activity until recovery work in O.7.5.

**Terminal coordinator evidence does not release shared capital.** O.7.2 claims remain fully held. A residual retry can proceed only if those conservative old holds plus the new claim still pass allocation. In particular, a filled/cancelled close may block a subsequent close until O.7.5 proves settlement. This prevents a partial-fill or cancellation report from becoming an implicit capital refund.

The shared authority can expose qualified factual snapshots even when risk limits or old holds are breached, so actual post-close positions can be reconciled. That read grants no order permission; allocation and final authorization still use the financial gates.

## Persistence and evidence

`PolicyState` supplies host-local atomic checkpoints and stale/uncertain writer fencing. The controller permits only one coordinator and checks the canonical recovery path when configured. Two instances cannot allocate conflicting hedge actions. Restored coordinators retain target/order/fill evidence and may request cancellation/reconciliation, but cannot create or resume orders. Existing account UID ownership and one canonical shared capital journal remain deployment prerequisites; multiple hosts are outside this MVP.

TDD red evidence: the initial test failed at collection because `hedge_coordinator` did not exist. Focused tests then drove actual runner issuance, target deduplication, partial/terminal proofs, retries, urgent dust/deadband handling, cost budgets, corrupted/missing checkpoints, policy changes, competing coordinators and final-send revocation. Real connector schedulers and REST throttlers use fake HTTP sessions; unsafe fee changes inside the wait produce zero HTTP requests, while valid evidence reaches ACK without releasing capital. All numerical values and observations in these fixtures are explicitly synthetic.

Reproduce:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_hedge_coordinator.py \
  test/hummingbot/strategy_v2/life_liquidity/test_hedge_coordinator_runner.py

conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o73-final-verified
```

The [regression artifact](evidence/life_o7_hedge_coordinator_regression.json) archives focused test nodes, the broader spot baseline audit, exact changed-line coverage and source hashes. The baseline audit includes extension tests without declaring full O.7 acceptance.

Final results: **59 focused tests passed; 1,735 broader regression tests passed, with zero failures and zero skips.** All 63 changed production Python files were measured; changed-line coverage passed both 80% baseline gates. Repository pre-commit/style checks passed. The data-gap timer and cheaper-snapshot replay regressions each failed before their checkpoint-ordering fixes and passed afterward.

## Remaining gates

- **O.7.4:** qualify and assemble market depth/fees, mark/index, funding/tier/collateral feeds and available edge; monitor carry after quote-session expiry. Source labels and typed injected providers are a contract, not proof of exchange authenticity.
- **O.7.5:** assemble authenticated SWAP fill/cancel/account evidence, settle/release capital, recover interrupted ownership and explicitly rearm after cold restart; prove joint session successors. Tests supply terminal WAL evidence at this seam; the coordinator never infers it from ACK.
- **O.7.6–O.7.7:** protected inventory execution and complete extension acceptance map/replays.
- **D/R:** demo, live numerical calibration and real-account eligibility. No credentials or live orders were used.
