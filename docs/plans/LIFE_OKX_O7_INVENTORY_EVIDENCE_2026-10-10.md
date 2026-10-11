# O.7.6 — Protected Inventory Execution

Date: 2026-10-10 (America/Los_Angeles). **O.7.6 is complete offline for the normalized contract below. O.7.7 remains open. Production trading remains disabled.**

## Included scope and API

`InventoryExecutor` is an explicitly installed spot inventory coordinator. Its separate immutable campaign owns BUY/SELL gross quantity, fixed pre-execution benchmark, absolute UTC deadline, child/notional/slippage/impact/participation limits and a separate execution/exit budget. Protected children retain the existing account parent session/epoch and cannot extend its deadline. This is an inventory execution policy, with no price-path or forced-decline target.

Assembly binds the existing controller, quote action journal, protected sender/WAL, reservation ledger, fill attributor and execution budget to the inventory ledger and mode journal. `enter_inventory(operator_id=...)` drains MM, `propose(intent_id)` returns an actual protected V2 child, `reconcile(cancel=...)` settles qualified history, and `return_to_mm(operator_id=...)` requires fresh cancellation/reconciliation before operator return. Restore uses the same immutable policy and paths with `create=False`; it never grants permission to replay an old config. Assembly/scheduling is explicit; there is no new CLI or automatic production installation.

The injected observation supplies bid/ask, explicit fee rate, expected impact, depth and cumulative public/known-own volume from the persisted campaign anchor. Source labels identify a contract; they do not authenticate a feed or prove demand is independent. Complete normalized spot account/regular/algo/trade and runner scope is required. If SWAP/shared capital is installed, entry/return also requires the existing complete joint proof.

## Financial and quantity invariants

- One serial maker child uses `LIMIT_MAKER`, actual V2 authorization, `OrderExecutor`, WAL-before-send and the final protected callback. BUY retains ordinary account quantity/notional risk limits; SELL preserves the physical balance floor fixed before execution. Neither can overspend reconciled balances or gross filled-plus-pending target quantity.
- Near-bid BUY and near-ask SELL are rechecked against the fixed benchmark, lot/tick/minimum rules, child/notional/target caps, depth, participation, slippage, expected impact and observed fees. Changes before the final callback revoke permission. Public volume growth cannot raise the hard budgets. Known own volume includes reconciled fills since the persisted campaign anchor, even if the source reports zero own volume.
- The execution/exit budget is separate from the MM service subsidy. Estimated adverse benchmark cost, impact and fees reserve capacity before send. Actual signed quote fees and LIFE fees converted through the qualified independent fill value report once. Favorable fills and rebates affect net shortfall but cannot refund gross adverse spending capacity. Independently attributed gross loss remains an additional floor.
- Partial fills remain factual while unfilled quantity and economic holds stay pending. Cancel ACK, missing/changed history, foreign regular/algo orders, balance mismatch, stale proof, disconnect and unverified runner events never release the remainder. Bound durable terminal proof plus terminal WAL/reservations release canceled capacity; only then may a new intent use the remaining target.
- Expiry reports completed/partial/expired and residual gross quantity. Creation is fenced, cancellation/reconciliation can finish, and no market order sweeps the remainder. Parent expiry is also binding. Pausing or stopping does not close a spot holding or SWAP position.

## Mode and recovery ordering

The mode journal commits DRAINING before awaiting MM cancellation and RETURNING before awaiting inventory cancellation. Both spot MM and SWAP final sends are denied outside armed MM mode. Account reconciliation also fences queued inventory creation while the snapshot is in flight. Parent reference/market-successor transitions cannot bypass the inventory mode guard, even with an otherwise complete spot-only receipt.

Issuance persists PREPARING before the quantity reservation, then the action claim/config before returning the child. A dispatched-before-WAL gap is proven unsent only after runner halt, absence of wire IDs and complete account scope; an uncertain send is never retried. Terminal settlement persists APPLYING before terminal quantity release, economic settlement and action cleanup; COMMITTED is last. Fresh inventory objects replay compatible real file-backed journals idempotently, retaining the original balance floor, benchmark and target.

Restart remains disarmed until fresh proof and explicit operator rearm. Missing journals, policy changes, stale writers, clock rollback, runtime HALT and stop deny sends. The account UID ownership lock remains held until inventory collector cancellation cleanup and the other safety tasks finish. These tests inject exceptions/cancellation at checkpoints and reopen inventory objects on the existing synthetic controller; they are not full process-kill or power-loss tests.

## TDD and acceptance evidence

The focused suite has **55 passing cases**. It covers actual BUY/SELL executor sends and partial/terminal accounting; sixteen final-wire faults; inflated volume and known own fills; immutable benchmarks and signed fees; five fresh-object issuance/settlement cuts; dispatched-before-WAL recovery; unknown terminal scope; target completion and fee breaches; stop/invalid configuration; MM queue/live-order draining; installed SWAP joint scope; and successor/reference fencing.

Observed Red cases included account lock release before inventory cancellation cleanup, direct reference epoch transition during inventory, and queued inventory authorization while an account snapshot was in flight. The corresponding regression cases pass after the ownership and transition/send guards were added. No test values are live defaults.

| Requirement / case | Verified O.7.6 slice |
| --- | --- |
| P7.1–P7.2 | Separate campaign, bounded balances/quantity, explicit maker semantics and protected actual runner children |
| P7.3–P7.4 / A37 | Anchored public volume minus known own, fixed hard caps, depth/impact/slippage/fee checks at final send |
| P7.5 / A12 | Completed and expired partial/unfilled reports, retained cancel-ACK remainder, no sweep |
| P7.6–P7.7 | Durable MM/inventory transitions, SWAP drain, reference/successor fence and stop ownership |
| P7.8 | Fixed benchmark shortfall, explicit converted fees, separate gross budgets and terminal settlement replay |

These are normalized offline slices. The detailed requirement roots and extension acceptance classification remain for O.7.7; D/R proofs are separate.

Reproduce:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_inventory_runner.py

conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o76-acceptance
```

The broad audit passed **1,944 tests**, with zero failures/skips and 27 warnings. Changed-line coverage is **89.62%** against the feature baseline and **90.77%** against the recent baseline; every changed production file is measured. Repository pre-commit checks passed.

The [regression artifact](evidence/life_o7_inventory_regression.json) retains the broad audit results, changed-line coverage, focused node outcomes and source hashes. Its nested O.6 audit still certifies **Spot Offline Complete**, with production permission false. Existing O.6 deferred classification and unchecked detailed requirement text are preserved; checked P7 children record this completed implementation slice.

## Remaining proof

Authenticated OKX feed/history collection, pagination, account cuts, fee/volume/depth normalization, actual runner/database assembly, process-loss behavior and exchange maker enforcement remain D/R. Dedicated LIFE-USDT spot plus the existing optional linear SWAP contract is assumed. One canonical host-local recovery directory and UID ownership are required when configured; isolated synthetic fixtures omit deployment configuration. There is no cross-host coordination or history pruning.

All numbers in tests are synthetic. Inventory deadlines can be shorter than the parent session but cannot extend it; a fresh parent campaign requires explicit assembly and policy. Reporting uses gross executed quantity, with fees separately affecting net physical holdings. Post-expiry cancellation requires a running controller; stopping is not position handoff. **O.7.7 combined extension replay/map, Full Offline Complete and live eligibility are not claimed.**
