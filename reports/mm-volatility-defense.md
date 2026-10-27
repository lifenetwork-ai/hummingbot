# MM volatility defense implementation

Implementation of the [ordered TDD plan](../docs/plans/MM_VOLATILITY_DEFENSE_TDD_PLAN.md).
Date: 2026-10-28 (Asia/Ho_Chi_Minh). Runtime acceptance remains open until its
own phase has run; baseline or earlier release evidence is not new acceptance.

## V0 — Baseline contracts

The existing MM base already produces three weighted levels on spot/perpetual:
at reference 0.1, the 0.4/0.9/1.8% levels produce the six proposed prices and
50/30/20 allocations within each side of a 200-quote total budget. The deployed
catalogue still selects spot/perpetual/both and two equally weighted levels.

Integration hooks: `LifeMMController.update_reference(sample_guard=True)` owns
policy sampling; ordinary readiness/send calls use cached observations.
`ServiceHost.mm_quote_valid` is called through `BotOwnership.mm_quote_check` at
intent and final transport. Existing controller `StopExecutorAction` preserves
positions. `BotOwnership._mutate_recovery` supplies fenced JSON checkpoints.
`SpreadPolicyStatus`, guard events and the service publication supply typed API
and history views without exchange calls from FE polling.

The new baseline tests are characterization, so they do not claim a Red/Green
runtime fix. Deterministic candle and clock cases follow in the owning phases.

Verification: five characterization cases pass. Candle fixtures reuse the
completed/forming/gapped feed helper in `test_life_mm_spread.py`; a separate wall
and monotonic clock fixture prevents offline time from earning recovery credit.
