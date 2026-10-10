# O.7.4 — Qualified carry monitoring

Date: 2026-10-10. **O.7.4 is complete offline for the contract below. Full O.7 remains open. Production trading remains disabled.**

## Scope and stream qualification

`CarryMonitor` is explicitly installed against the existing shared capital authority. Its immutable policy binds account UID, LIFE-USDT-SWAP, an accounting-history anchor, freshness/skew limits, carry horizon, maximum schedule gap, adverse funding-rate floor, funding spending budget and basis limit. These inputs have explicit millisecond, base LIFE, USDT and basis-point units. Test values are synthetic and are not live defaults.

The provider supplies separate typed account, mark, index, funding and fee/tier observations. Mark/index must match the qualified shared account snapshot; benchmark/internal prices cannot substitute for them. Each stream has its own sequence and observation time. UID, instrument, source identity, complete account scope, finite values, future/stale data, time skew, sequence rollback and same-sequence mutation are checked before permission. Funding and account terms cannot borrow a fresh mark timestamp.

These are **injected offline source contracts**, not authenticated OKX feed collectors. Real endpoint/WS assembly, pagination, settlement currency/sign normalization, eligible USDT collateral valuation and exchange account-mode qualification remain demo/real integration gates. No credentials or exchange orders were used.

Supported capital scope remains account mode 2, cross margin, explicit USDT collateral, linear LIFE-USDT-SWAP and ONEWAY/HEDGE. Dynamic whole-notional initial/maintenance tiers are validated through `CapitalPolicy`; qualified maker/taker costs use a conservative nonnegative floor, with no rebate financing. The original shared authority still applies its own immutable limits in addition to the dynamic carry check.

## Funding schedules, liabilities and budgets

Current and next settlement timestamps determine the observed cadence; there is no fixed eight-hour assumption. A shorter cadence increases the number of stressed events over the explicitly configured horizon. Missing/past/non-increasing/unsupported schedules fail closed. Tests exercise one-, two-, four- and eight-hour intervals and rate sign changes.

The conservative forecast is:

```text
events = 1 + floor(configured_horizon / observed_interval)
gross_base = actual_long + actual_short + all independent pending SWAP OPEN quantities
price_bound = max(mark, index, pending limit prices) × (1 + configured price stress)
rate_bound = max(abs(observed funding rate), configured adverse rate floor)
future_hold_USDT = gross_base × price_bound × rate_bound × events
```

Pending closes never refund this hold before settlement. Near-zero net delta does not remove gross funding, basis or margin risk. The signed next-event estimate describes expected payment on actual positions; expected income never finances new orders. The forecast intentionally remains conservative for hedged or receiving positions and must be calibrated before live eligibility.

Funding bills use immutable IDs, settlement timestamps and signed USDT payments: positive receipt, negative debit. The provider must supply complete history from the policy anchor through an explicit coverage time. Prior bills cannot disappear, mutate or repeat. A schedule rollover past a previously announced settlement requires history coverage through that settlement, including explicit complete zero-payment coverage when appropriate. Account inclusion IDs must match the supplied bills exactly.

Two account assertions are mandatory:

- Eligible collateral **already includes settled funding**. Those bills update durable attribution and gross spending diagnostics, without another subtraction from account collateral.
- The existing snapshot funding liability represents **known unpaid funding only**, excluding settled bills and future forecast holds. Unknown semantics are rejected.

Capital therefore holds `known unpaid liability + future stress`. The funding spending gate uses `gross settled debits + known unpaid liability + future stress`. Receipts cannot erase gross debits. The monitor does not add an expected receipt, future sale proceeds or a pending close's released margin to available funds. Full joint funding/NAV ledger settlement and capital recycling remain O.7.5; this stage does not infer a balance delta from a bill alone.

## Order and lifecycle integration

Both protected senders check prospective claims before allocation and repeat the installed carry gate at final network authorization. A fee/tier/funding/basis change after dispatch revokes the queued request. Real V2/OrderExecutor/OKX scheduler and REST throttler tests use fake HTTP sessions; a shock during the throttler wait sends zero requests. Successful ACK retains the capital claim.

The hedge coordinator incorporates the qualified fee and stressed funding floor into its existing execution-cost, available-edge and persistent campaign-cost checks. The initial cost includes the child's own limit-price bound before publishing its capital claim. This avoids self-revocation when that claim raises the monitor's price bound; later cost growth still revokes the send. A regression failed at actual runner authorization before this correction and passed afterward.

A failed carry decision blocks quote planning and emits stop actions for known nonterminal spot/SWAP children. It does not mark positions closed or refund claims. Joint exchange cancellation confirmation, position/fill settlement and successor activation remain O.7.5.

The readiness-independent safety tick calls carry qualification before unrelated quote gates, including after the quote session expires. Tests advance beyond expiry with remaining positions, apply a funding settlement, then breach collateral/margin and verify continuing observations with the session still expired. Monitoring continues while the controller/safety loop runs; stopping the process is not a position-close operation. Durable recovery, deployment monitoring and operational handoff remain O.7.5 and D/R.

## Persistence and TDD evidence

The carry journal binds its policy and canonical shared-capital path. Observed clock advances checkpoint before provider reads, including failed reads, so a feed gap cannot erase a later clock rollback. Qualified newer streams and funding history checkpoint before a financial refusal, so replaying a cheaper prior snapshot cannot restore permission. `PolicyState` provides atomic host-local writes and stale/uncertain writer fencing. Missing/corrupted state, changed policy, competing writers, provider failure, checkpoint failure and clock rollback deny permission. Restore retains evidence but requires fresh qualification; it does not grant joint recovery/retry authority.

Red evidence: the first focused suite failed collection because `carry_monitor` did not exist. The feed-gap/clock-rollback regression also failed with `CARRY_READY` before the clock checkpoint fix, then passed with `CARRY_CLOCK_ROLLBACK`. Green unit and runner tests cover stream substitution/freshness/identity, dynamic cadence and sign, settlement deduplication/coverage, unpaid-plus-forecast funding, zero-delta gross risk, price/tier/fee shocks, prospective/final revocation, cold restore, storage faults, hedge cost holds and post-expiry monitoring. No live numerical input is inferred from these fixtures.

Reproduce:

```sh
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_carry_monitor.py \
  test/hummingbot/strategy_v2/life_liquidity/test_carry_runner.py

conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o74-final-verified
```

The [regression artifact](evidence/life_o7_carry_regression.json) archives focused node results, the broader spot baseline audit, source hashes and exact changed-line coverage. The broader audit executes extension tests while retaining its **Spot Offline Complete** verdict; it does not declare full O.7 acceptance.

Final result: **99 focused tests passed; 1,834 broader regression tests passed, with zero failures and zero skips.** All 64 changed production Python files were measured. Changed-line coverage passed the 80% gates at **89.97% feature / 93.87% recent**; the new carry module measured **96.83%** changed-line coverage. Exact denominators and test nodes are archived in the artifact. Repository pre-commit/style checks passed. Optional carry telemetry capture/restore passes while the frozen spot replay remains unchanged.

## Remaining gates

- **O.7.5:** joint SWAP order/fill/account recovery, funding/NAV attribution, proven capital settlement/release, explicit cold-restart rearm and cross-product session successors.
- **O.7.6:** protected inventory execution and mode transitions.
- **O.7.7:** full combined extension replay and acceptance mapping before Full Offline Complete.
- **D/R:** authenticated feed assembly, demo evidence, numerical calibration and real-account eligibility. Production permission remains disabled.
