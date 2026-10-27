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

## V1 — Existing spread floors

Verified every level at zero/moderate/shock NATR and explicit static fallback
with `min_multiplier: 1`, ordered spreads and existing fee/rounding behavior.
Legacy low-multiplier/tighter-fallback settings remain readable. The MM guide
now explains three-level weights and the existing floor settings.

Verification: 31 focused spread/characterization tests pass. This phase needs
no new runtime flag; defense-specific validation is implemented with V2's schema.

## V2 — Reduced sizing and stale quote protection

Red: six new schema/raw-NATR/sizing/controller cases failed before implementation;
an additional task-context case failed because per-level authority did not exist.
Green: raw NATR is retained separately from the clamped spread multiplier, reduced
sizing composes with bootstrap/skew allocation, recovery needs distinct candles
and elapsed monotonic time, and final terms are checked against their own level.
Existing quote cancellation is coalesced, preserves positions and retains pending
reservations; no replacement occurs while an executor is shutting down.

The existing session context now carries optional level identity into async
connector tasks without changing the session authority tuple. Legacy policies
retain their existing send signature. Defense remains rejected at catalogue
admission until V3 installs durable pause/recovery; this commit cannot enable a
partial defense in the service.

Verification: 107 controller/spread/skew/session tests pass; a separate 134-test
selection including service and position executors passed before five additional
bootstrap/skew/per-level cases were added. The regression Make target includes
the new defense tests. Default deployed YAML and original file-mode policies
remain unchanged. An initially incomplete bootstrap test book was corrected to
supply real bid/ask entries; no production guard was weakened.
