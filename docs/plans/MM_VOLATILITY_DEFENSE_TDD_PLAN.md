# LIFE / OKX volatility defense — incremental TDD plan

Date: 2026-10-28 (Asia/Ho_Chi_Minh).
Status: planned; implementation and acceptance checklists below are open.

Improve quote protection and automatic recovery while keeping the existing
Hummingbot execution engine. Priorities are explainable liquidity provision,
bounded capital allocation and service availability. This is a follow-up to the
completed [enhancements plan](LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md), not a reopening
of its historical acceptance results. It does not claim profitable execution.

Implement phases in order. For each new behavior, capture Red, implement Green,
refactor, record evidence and make a local commit before proceeding. Existing
correct behavior gets characterization coverage; do not invent a failing test
for a feature already implemented. Report concrete blockers without weakening
the acceptance rules. Planning approval is not approval to deploy or trade live.

## 1. Scope and reuse

| Capability | Baseline and planned work |
|---|---|
| Multiple levels and allocation weights | Already supported by the MM base. Configure two, three or more levels through YAML; no three-level engine |
| Dynamic spread | Extend the existing `SpreadPolicy`, completed-candle NATR and maker fee helper |
| Base spread floor | Reuse `min_multiplier >= 1` and fallback spreads at least as wide as their corresponding base levels; no duplicate floor setting |
| Smaller quotes during elevated volatility | Add one bounded size factor, composed with existing allocation, bootstrap and spot inventory skew |
| Extreme volatility | Add a temporary quote pause with hysteresis, observable recovery and restart protection |
| Price/data qualification | Reuse outside-book filtering, strict/bootstrap guards, freshness and absolute price bounds |
| Lifecycle and persistence | Reuse controller stop actions, position executors, ownership/final-send guards and PostgreSQL recovery checkpoints |
| Operations | Extend existing status, guard events, English guidance, logging and handler-generated OpenAPI |

Relevant implementation: [MM base](../../hummingbot/strategy_v2/controllers/market_making_controller_base.py),
[LIFE controller](../../controllers/market_making/life_mm.py),
[spread policy](../../hummingbot/strategy_v2/utils/mm_spread.py),
[quote guard](../../hummingbot/strategy_v2/utils/quote_guard.py),
[inventory skew](../../hummingbot/strategy_v2/utils/inventory_skew.py),
[bootstrap](../../hummingbot/strategy_v2/utils/listing_bootstrap.py),
[ownership](../../hummingbot/core/bot_ownership.py) and
[state store](../../hummingbot/model/bot_state_store.py).

The repository already provides [OKX Spot Candles](../../hummingbot/data_feed/candles_feed/okx_spot_candles/okx_spot_candles.py)
and its factory registration. Do not build another trade-stream/candle feed,
service, scheduler, cache backend, price engine or generic risk framework.
Keep the three existing reference formulas and their persisted session anchors.
Defer blended fair-value estimation, adverse-selection estimation, target-profit
optimization and separate volatility sensitivities per level.

The pre-plan focused review passed 92 existing spread, quote-guard and spot-candle
tests. That verifies the baseline only, not the additions described here.

## 2. Configuration contract

All policy belongs in the existing market YAML and pinned session catalogue.
API controls remain limited to session selection/timing/Start/Stop/Switch. No API
may override thresholds, allocations, fallback, weights or reference sources.
No new `.env` setting is required. Do not change production budgets or enable
these additions in the default LIFE catalogue as part of implementation.

The following is a **proposed market fragment for deterministic tests**, not a
complete catalogue or recommended listing policy. `defense` is not implemented
yet. Its absence/null preserves existing spread and sizing behavior.

```yaml
buy_spreads: [0.004, 0.009, 0.018]
sell_spreads: [0.004, 0.009, 0.018]
buy_amounts_pct: [50, 30, 20]
sell_amounts_pct: [50, 30, 20]
spread_policy:
  fee_floor: true
  volatility:
    enabled: true
    interval: 1m
    length: 14
    target_natr: "0.01"
    min_multiplier: "1"
    max_multiplier: "3"
    max_age_seconds: 120
    on_unavailable: static
    fallback_buy_spreads: ["0.004", "0.009", "0.018"]
    fallback_sell_spreads: ["0.004", "0.009", "0.018"]
    defense:
      reduce_at_natr: "0.02"
      stop_at_natr: "0.05"
      resume_below_natr: "0.015"
      reduced_size_factor: "0.5"
      recovery_seconds: 60
```

- Use raw NATR as a fraction (`0.02` means 2%), not the clamped spread multiplier.
  Preserve the raw cached value and completed-candle identity in `SpreadPolicy`;
  a multiplier capped at three must not hide further volatility increases.
- Require finite `0 < resume_below_natr < reduce_at_natr < stop_at_natr`,
  `0 < reduced_size_factor < 1`, and an explicit positive bounded recovery delay
  (at most 3,600 seconds). There are no implicit risk thresholds.
- Defense requires volatility, fee floor and existing quote guards to be enabled.
  Both strict and explicitly bounded bootstrap modes remain supported.
- Defense requires `min_multiplier >= 1`. Static fallback must cover every level,
  be ordered, and be no narrower than the matching base spread. Preserve these
  invariants after fee composition and exchange rounding.
- Validate new restrictions only for enabled defense. Existing catalogues and
  pinned sessions with `min_multiplier: 0.5` remain readable and unchanged.
  Preserve stored catalogue hashes and command retry identities; do not rehash
  old snapshots merely because a new optional field exists.
- Buy/sell weights normalize across both sides. The example gives half the
  total allocation to each side, then 50/30/20 within each side. Weights are not
  three independent budgets. Small/minimum-size levels may be skipped.

## 3. Behavior and availability contract

### State transitions

The state below is nested defense status; retain the existing spread status and
readiness contracts. Evaluate only bounded cached observations in the existing
control loop. A severity increase takes effect immediately on a valid sample.

| Condition | Required behavior |
|---|---|
| Fresh NATR below `reduce_at_natr`, no prior restriction | Normal sizing and effective spreads |
| NATR at/above `reduce_at_natr` but below `stop_at_natr` | Reduced sizing; maintain dynamic/base/fee spread floors |
| NATR at/above `stop_at_natr` | Pause new quotes, request cancellation of working quote remainders and retain filled positions |
| Reduced state, NATR below `resume_below_natr` | Return to normal only after recovery qualification |
| Paused state, NATR below `reduce_at_natr` | Recover to reduced sizing after qualification; require a new qualification below `resume_below_natr` before normal sizing |
| Missing, malformed, gapped or stale candles with `on_unavailable: wait` | Wait and request quote cancellation; automatically reconsider fresh observations |
| Unavailable candles with explicit static fallback and no recorded extreme-volatility pause | Use fallback spreads and reduced sizing only while all mandatory checks pass |
| Unavailable candles after an extreme-volatility pause | Stay paused; static fallback cannot erase a known volatility breach |

Qualification requires the configured elapsed monotonic duration and at least
two distinct fresh completed candles satisfying the recovery condition. Repeated
status/final-send checks or repeated use of one candle do not advance sample
counts. An unhealthy observation resets qualification. Restart never credits
offline time, and the nominal delay is not a promise to resume before the next
qualifying candles arrive.

NATR is a closed-candle measure and is not an instantaneous price-jump detector.
Keep existing fresh-book/reference-deviation/range checks active for current
prices. This scope does not add or claim a new tick-based circuit breaker.
If independent intrabar return thresholds become necessary, propose that scope
separately rather than silently adding a second volatility estimator.

### Sizing and existing orders

1. Compute the effective allocation as `min(market allocation, active bootstrap
   allocation if applicable) * defense size factor` (normal factor is one).
2. Derive weighted level amounts at their proposed prices. Apply existing spot
   inventory skew using those effective baseline quantities and scale its result
   to the same effective allocation. Perpetual has no spot inventory skew.
3. Quantize downward; recheck fees, available balances, exposure, outflow,
   bootstrap bands and post-only rules using the final price and amount. Reducing
   size can make a flat fee or minimum order size decisive. Skip invalid levels;
   never enlarge them or redistribute rejected size automatically.
4. When entering reduced/paused mode or tightening a required spread, evaluate
   existing orders as well as new proposals. Reuse cancel/refresh with
   `keep_position=True`; do not wait for the ordinary 60-second refresh to remove
   newly unsafe working quotes. Preserve partial fills and their accounting.
5. Reserve active, pending and shutting-down allocations until terminal proof.
   Do not stack replacement orders over an unconfirmed cancellation. Coalesce
   cancellation requests, re-evaluate the latest policy before replacement, and
   avoid repeated cancel/recreate loops when quantized terms already comply.

The final transport check must reject a proposal queued before a spread/size/
state restriction. Validate its own level and final notional, not just the
smallest spread across all levels. Use existing intent/authority checks to cover
aggregate reservations and concurrent submissions without a second order ledger.

### Listing, lifecycle and persistence

No candles at listing is a distinct condition from observed extreme volatility.
Use the explicitly selected wait/static policy. Static fallback does not supply
a trustworthy price: strict mode still waits for qualified outside liquidity;
bootstrap still requires its explicit anchor, finite deadline, small allocation,
spending protection and qualification rules. A volatility wait, restart, random
step or profile switch never renews bootstrap permission.

Persist only the small extreme-volatility pause latch and the policy needed to
recover it, through existing owner-fenced PostgreSQL recovery JSON. Scope it to
the bound account/environment and market/pair; do not key it by profile, random
step or session. Reuse the existing account binding. Bound the state to configured
markets; store no candle series and perform no per-tick DB writes.

Close in-memory admission and initiate cancellation before awaiting a latch
write. Persist transitions before reporting a durable transition or reopening
admission. A failed write follows existing persistence/ownership failure rules;
it cannot be reported as a successful recovery. A crash before a latch is durable
uses existing unclean-recovery fencing; it must not silently enable fallback.

Stop/Start, clean restart, market switching and random rotation must retain a
pause. On restore, reset recovery timers/sample counts and require fresh data.
While a pause remains latched, reject removing/changing that market's defense
policy on re-admission with an actionable English conflict; normal session API
commands cannot reset it. Fresh-data recovery under the bound policy clears the
latch. Operator-reviewed maintenance remains the existing exceptional path, with
no new bypass/reset endpoint. Do not turn this into permanent policy binding
after ordinary recovery.

Keep the existing joint readiness behavior in `both` mode: one market's required
wait/pause prevents joint quote admission and cancels through the current path.
Expose which market caused the wait. Do not promise independent market uptime or
redesign joint readiness here. A reduced-size state alone remains admissible.

Temporary volatility/data waits keep the process, management API and recovery
loop alive. They never restart an operator-stopped or expired session, change
its duration, clear a durable risk HALT, force liquidation, retry an uncertain
exchange mutation or override ownership/recorder failure handling.

The new durable defense initially targets the PostgreSQL service runtime. Reject
defense in unsupported file/unmanaged mode explicitly; retain original file-mode
behavior when disabled. No new table or migration is expected for bounded JSON
state. If implementation proves one necessary, add the smallest idempotent
snake_case migration and document why; do not prebuild a generic persistence layer.

## 4. Ordered implementation checklist

### V0 — Lock contracts and characterize the baseline

- [ ] Locate exact controller, owner/final-transport, checkpoint and status hooks;
  add existing test coverage locations to implementation evidence.
- [ ] Characterize three weighted levels, 200-quote allocation split, original
  two-level behavior and spot/perpetual/both modes with frozen fixture data.
- [ ] Pin deterministic candle/fake-clock fixtures and the state, sizing,
  fallback and restart contracts above before changing runtime behavior.

### V1 — Base spread floor using existing configuration

- [ ] Verify `min_multiplier >= 1`, explicit fallback floors, maker fees and
  BUY/SELL tick rounding preserve each base level. Cover zero/flat NATR and
  ordered levels that round to the same price.
- [ ] Retain compatibility for disabled defense and older pinned snapshots.
  Pin the cross-field expectations for V2's optional schema; do not add a second
  base-floor toggle or modify legacy defaults globally.
- [ ] Document the three-level YAML example without changing default production
  spreads, budget, market availability or risk settings.

### V2 — Reduced sizing

- [ ] Red/Green the optional schema, raw-NATR cache and reduction/recovery logic;
  include the defense-specific floor/fallback validation from V1. Final-send
  checks only read a fresh decision and cannot advance recovery. Until V3's
  pause/persistence behavior is complete, reject enabled defense at service
  admission rather than expose a partially implemented safety contract.
- [ ] Red/Green composition with weighted levels, bootstrap and spot skew,
  including flat fees, min size, rounding, balances, outflow and exposure caps.
- [ ] Red/Green immediate reconciliation of oversized working quotes and queued
  proposals; retain full reservations until cancellation/terminal confirmation.
- [ ] Prove disabled behavior and stable quantized quotes do not acquire new
  cancellation churn, DB reads, candle tasks or repeated indicator calculation.

### V3 — Extreme-volatility pause and durable recovery

- [ ] Red/Green threshold equality, normal/reduced/paused transitions, hysteresis,
  distinct completed-candle recovery, stale/gapped data and static/wait fallback.
- [ ] Red/Green latch writes/restoration with real PostgreSQL, owner fencing,
  rollback/failure, clean/unclean restart and reset recovery timing.
- [ ] Cover Stop/Start/Switch, disabled-market return, random boundaries,
  until-stopped sessions, timed expiry, policy-change rejection and existing HALT.
- [ ] Cover partial fill during cancel, slow/missing cancel acknowledgment,
  duplicate/late events and changed payloads at final send. Never double-place
  or infer that sending cancel means the exchange has removed an order.
- [ ] Verify listing without candles/outside depth, bounded bootstrap expiry and
  delayed market qualification. Static fallback cannot bypass a saved pause.
- [ ] Verify joint both-market wait/cancellation and healthy process/API while
  paused. Existing uncertain-outcome handling remains authoritative.

### V4 — Operator/API integration

- [ ] Extend existing typed spread status with raw NATR, defense state, effective
  size/allocation, sample/freshness times, reason, recovery progress and whether
  cancellation is pending. Distinguish requested cancellation from observed
  working orders; unknown state must not look healthy.
- [ ] Publish English labels such as `Trading with smaller orders`,
  `Waiting for volatility to settle`, and `Waiting for market data`, with a clear
  next action. Report `automatic_recovery_expected` without promising an ETA.
- [ ] Reuse guard-transition history and severity logging: WARNING on restriction,
  INFO on recovery, bounded DEBUG diagnostics, ERROR for internal/persistence
  failures. Avoid per-tick/per-poll log spam and unbounded event storage in memory.
- [ ] Update handler/model annotations, `make swagger`, `make swagger-check`,
  [FE guide](../fe-integration.md), [MM controls](../life-mm-controls.md) and
  [OKX operations](../okx-operations.md). Keep English API text, UTC timestamps,
  Decimal strings, YAML ownership and development-only Swagger.
- [ ] Extend existing snapshots/endpoints; prove FE polling does not perform
  exchange calls, advance policy or introduce N+1 queries. No dashboard app here.

### V5 — Local acceptance, load and final Demo compatibility

- [ ] Run focused Red/Green suites, `make test-cloudrun-regression`, relevant
  real-PostgreSQL tests and Swagger checks against the final source.
- [ ] Extend the existing offline simulator/image harness with deterministic
  volatility and lifecycle faults, real adapters/executors/recorder and independent
  fill/fee/position truth. Explicitly enable defense in fixtures and keep the
  unchanged legacy/disabled scenarios as regression coverage.
- [ ] Build and verify one immutable deployment image against its source. Run
  image E2E for spot-only plus perpetual/both, three levels, wait/static listing,
  slow cancellation, pause/recovery and restart. Check logs as well as HTTP errors.
- [ ] Run a short diagnostic first, then existing mixed/burst and unchanged
  sixty-minute zero-unexpected-error acceptance gates on the final image. Include
  enabled-defense transitions in additional declared windows; intentional waits
  must not reduce the offered healthy load or conceal missed requests/fills.
- [ ] Reuse the [declared load budgets](../life-mm-load-tests.md): p95 <= 500 ms,
  p99 <= 1 s, stop/cancel <= 5 s under responsive simulated exchange conditions,
  RSS <= 1.5 GiB and the existing memory/backlog/SQL budgets. Deliberately delayed
  cancellations are separate fault windows, with no replacement before proof.
- [ ] Measure decision-to-cancel latency, rejected stale submissions, cancellation
  churn, state/recovery correctness, task/cache retention and query counts. Recovery
  timing must account for candle completion and configured qualification; ordinary
  healthy Start/Switch budgets do not imply bypassing an active defense wait.
- [ ] Extend existing synthetic economic replay for one-sided pressure, price
  gaps and missing liquidity. Compare allocation, outstanding exposure, time
  paused, two-sided presence and turnover under identical assumptions; do not
  claim a better profit outcome from a synthetic fill model.
- [ ] After local/image/load acceptance, run a bounded OKX Demo compatibility
  check on listed instruments using the existing explicit Demo configuration.
  Verify candle routing, weighted maker quotes, final size/fee checks and
  position-preserving Stop. Record unmet prerequisites as blockers, not passes.
- [ ] Record report and sanitized JSON evidence under `reports/`, raw logs under
  ignored `build/`; update this checklist only for actually verified outcomes and
  commit the completed phase. Retain failed attempts and explain their resolution.

OKX Demo is the final compatibility check, after local testing. Do not induce
volatility, self-trade, alter live credentials or wait for a natural crash to
prove deterministic transitions. Record its actual scope separately; local
acceptance must prove all fault/recovery cases. A blocked or unrun Demo check
remains open unless the user explicitly waives it. No Cloud Run staging or live
LIFE trading is required by this plan. Real listing thresholds and capital
allocation remain an operational decision after instrument rules, inventory and
budget are known.
