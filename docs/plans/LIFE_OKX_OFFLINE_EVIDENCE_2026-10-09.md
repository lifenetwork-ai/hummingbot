# LIFE/OKX offline evidence checkpoint — 2026-10-09

## Scope and verdict

This checkpoint covers synthetic LIFE spot scenarios, selected joint spot/SWAP
risk contracts, and an advisory inventory execution journal. It closes **O.2 spot risk/economics**, **O.3 spot lifecycle**, **O.4 spot recovery**, and **O.5 spot telemetry/adversarial replay** offline acceptance.
O.6–O.7 remain open in
the [implementation plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).
No demo or production order was sent. All quoted balances, limits, prices,
fees, latencies, and queue assumptions in these tests are simulation values;
none are live defaults.

| Gate | Current evidence | Still needed offline |
| --- | --- | --- |
| O.2 spot economics/risk | **Offline complete.** Real V2/executor/fake OKX acceptance now includes durable rolling capacity, reservation-derived stress, bounded degraded probes, protected exits with separate fee/loss budgets in both objectives, periodic authenticated-style fee refresh, independently valued LIFE transfers, late stop revocation, failed/saturated cancellation, capital/loss/markout and restart | No remaining O.2 spot offline requirement. D/R production installation, qualified observations and calibration remain; SWAP funding/basis/hedging remain O.7 |
| O.3 spot lifecycle | **Offline complete.** One real V2 runner/fake OKX session covers lost ACK, partial fill/fee, expiry/cancel, missing/foreign regular orders, foreign algo order, incomplete paginated and foreign completed-fill history, own-depth-qualified successor anchor, replacement quote with status-backed ACK, residual inventory, and journal restore | Demo connector behavior and real-account history/reference qualification remain D/R work; production order permission stays disabled |
| O.4 recovery | **Offline complete.** Eight child-process SQLite INSERT/UPDATE/commit boundaries, 16 combined runner action/recorder/cashflow/retirement interruptions, unknown-send retention, terminal proof, deduplicated transfer replay, and 22 account-bound manual-rearm cases complement the existing WAL/reservation/clock/HALT tests | D/R deployed storage/clock/account ownership and history completeness; physical power-loss behavior is an operating assumption; SWAP recovery remains O.7 |
| O.5 simulation/telemetry | **Spot offline complete.** Bounded quality-flagged telemetry, 14 risk scenarios, 14 frozen-seed queue/fill cases, 100x turnover/net-flat loss replay and byte-reproducible normalized evidence | D/R actual observation, queue/cost/latency calibration and liquidity KPIs; O.7 SWAP telemetry/scenarios; economic viability remains insufficient evidence |
| O.6 review | Scoped regression and changed-code coverage below | Full spot A01–A39 matrix, changed-code review, named reviewer and reproducible evidence bundle |
| O.7 SWAP/inventory | Joint pending-fill stress, conservative hedge decision, inventory child/exit-cost journal | Protected SWAP order gateway, authenticated fills/funding/margin, shared coordinator, inventory runner route and mode transitions |

## O.2 spot offline closure

Reviewed by Codex on 2026-10-09 against P4.1–P4.18 and the scoped acceptance cases below. This is repository code/test review, not an independent operator release decision. Production LIFE order permission remains disabled. Adapters are explicitly installed by fixtures; synthetic price/fee/depth/budget observations are never live defaults. Acceptance uses the real controller safety gate and listing/book/continuity gates; only the production release switch is replaced. Dedicated lower-level contract tests may isolate a single gate, as labeled in their fixtures.

Final O.2 closure regression: **1,168 passed, 28 warnings** across the five suites in the command below. All pre-commit checks pass. Changed-line coverage was not rerun for this closure; the previous coverage measurement remains historical and O.6 remains open.

### Completed implementation and Red → Green evidence

- Rolling/stress and recovery tests first failed on missing bindings. They now rebuild time-window capacity from durable attributed fills, count unresolved orders without opposing-side offsets, recheck actual executor sends, clip degraded probes and retain campaign capacity after terminal fills/restart. Missing/stale/model observations, deleted journals, changed policy and clock rollback deny new risk.
- LIFE transfer tests first failed because approvals had no exchange time and no independent transfer-value path. Bill times now match explicit approvals; qualified values are persisted once and interleaved chronologically with fills. Repeated scans/restart preserve physical balances and net external USDT value. Missing/model/stale values and failed checkpoints block attribution and the actual V2 route. Legacy USDT journals keep their existing replay contract without an implicit migration.
- Fee refresh previously had no runtime cache/lifecycle. Authenticated-style `_api_get` requests bind exact account, SPOT instrument and fee group. Refresh invalidates the old snapshot before awaiting I/O; overlap, overdue/error/rollback data cannot silently fall back to it. Actual executor final-send tests reject unavailable or increased rates.
- Exits previously had only a pure economic decision. Explicit post-only SELL exit actions now require cancel/account proof, exact immutable action/session/config/risk identity, independent price/depth, rounding/minimum size, no reversal including a conservative LIFE-fee allowance, fresh fee bounds when a fee binding is installed, and a separate durable exit budget. An isolated exit can reduce inventory already over a new-risk limit without relaxing quote limits. The profit gate is excluded from exit authorization; HALT, expiry and data gates remain mandatory. Unknown sends are never blindly retried, and outstanding/blocked exits report residual risk.
- Attributed exit fills carry a durable purpose and charge the exit budget's actual fee/loss floor automatically. In `liquidity_service`, these fills enter capital and execution-loss accounting once while leaving the service-subsidy ledger untouched. Terminal exchange/account/WAL/reservation proof settles capacity; unknown or partial exposure keeps its hold. Restart retains both identities and costs.
- The expanded combined replay found that an already dispatched healthy send could remain authorized after a late runner stop. The stop now latches in-memory revocation before either cancel checkpoint; the final send fails even if that checkpoint raises. Stops still dispatch on persistence failure. The five cancellation variants compose pending one-sided fills, fee repricing, loss, independent NAV, markout, rolling/stress and journal restore under normal, lost-ACK, saturated, late-stop and stop-checkpoint-failure conditions.

### P4 spot requirement mapping

All file names below are under `test/hummingbot/strategy_v2/life_liquidity/` unless noted.

| Requirement | Executable spot offline evidence | Remaining environment-dependent proof |
| --- | --- | --- |
| P4.1 balances/inventory/gross/net | `test_reservations.py`, `test_executor_protected_send.py`, `test_spot_session_replay.py`, `test_protected_exit.py` | D/R actual balances and production installation |
| P4.2 shared capacity | `test_reservations.py`, `test_account_lock.py`, shared quote/exit ledger in `test_protected_exit.py` | D/R deployed ownership; joint SWAP coordination O.7 |
| P4.3 partial fills/cancel release | `test_reservation_persistence.py`, `test_order_gateway.py`, `test_runner_fill_events.py`, `test_spot_session_replay.py` | D/R actual exchange/account reconciliation |
| P4.4 unknown/restart | `test_crash_recovery_replay.py`, `test_recorder_cold_restart.py`, `test_o2_revocation_replay.py` | D/R actual history completeness/retention |
| P4.5 rolling replenishment | `test_spot_risk_binding.py`, `test_o2_integrated_replay.py` | R calibrated side/window capacity |
| P4.6 stale feeds/latency/model | `test_runtime_risk_binding.py`, `test_o2_revocation_replay.py`, `test_controller_order_safety.py` | D/R qualified feed/account/latency/model observers |
| P4.7 capital/cashflows/A29 | `test_capital_risk_binding.py`, `test_life_cashflow_value.py`, `test_o2_turnover_replay.py` | R qualified opening values/transfers; SWAP funding O.7 |
| P4.8 latched HALT | `test_risk_priority.py`, `test_capital_risk_binding.py`, `test_runtime_risk_binding.py`, `test_o2_revocation_replay.py` | D/R actual data/operations; broader fault matrix O.4 |
| P4.9 markout/slow loss | `test_markout_runtime_binding.py`, `test_loss_budget.py`, `test_o2_integrated_replay.py` | R independently qualified horizons/cohort calibration |
| P4.10 action priority | `test_quote_action_dispatch.py`, `test_o2_revocation_replay.py`, `test_o2_integrated_replay.py`, `test_protected_exit.py` | D shared exchange/API behavior |
| P4.11 fees/net edge/service | `test_economics.py`, `test_fee_refresh.py`, `test_fee_quote_binding.py`, `test_reconciled_fill_attribution.py`, `test_inventory_subsidy_settlement.py` | R actual account tier, conversion, calibration |
| P4.12 bounded exits | `test_protected_exit.py` in both objectives: queue/send, no flip, slippage/depth/budget, partial fee/floor, terminal proof and restart | D/R production exit installation/calibration |
| P4.13 queued/retried/throttled revocation | `test_o2_revocation_replay.py`, `test_final_quote_send.py`, `test_protected_okx_send.py`, `test_spot_session_replay.py` | D/R real connector behavior and release gate |
| P4.14 WAL before send | `test_wal.py`, `test_executor_protected_send.py`, `test_controller_order_safety.py` | D/R live inputs; combined recorder fault coverage O.4 |
| P4.15 independent cancellation | `test_safety_watchdog.py`, `test_cancel_retry.py`, `test_request_budget.py`, five variants in `test_o2_integrated_replay.py` | D/R shared API headroom/timer; physical stalls/power assumptions remain |
| P4.16 one-sided stress | `test_spot_risk_binding.py`, `test_stress.py`, `test_o2_integrated_replay.py` | R calibrated depth/shocks; SWAP basis/funding/hedge O.7 |
| P4.17 starting inventory vs execution | `test_accounting.py`, `test_reconciled_fill_attribution.py`, `test_life_cashflow_value.py`, `test_o2_turnover_replay.py` | R qualified opening/cashflow values; SWAP funding O.7 |
| P4.18 identities/recovery probes | `test_runtime_loss_binding.py`, `test_subsidy_runtime_binding.py`, `test_recovery_probe_binding.py`, `test_protected_exit.py` | R calibrated budgets/probe policy |

### Applicable O.2 acceptance mapping

| Cases | Offline evidence |
| --- | --- |
| A06–A09 | `test_reservations.py`, `test_risk.py`, `test_final_quote_send.py`, `test_spot_session_replay.py`, `test_crash_recovery_replay.py` |
| A21–A24 | `test_fee_refresh.py`, `test_fee_quote_binding.py`, `test_economics.py`, `test_protected_exit.py`, `test_o2_revocation_replay.py`, `test_safety_watchdog.py` |
| A27–A28 | `test_markout_runtime_binding.py`, `test_runtime_loss_binding.py`, `test_recovery_probe_binding.py`, `test_o2_integrated_replay.py` |
| A29 | `test_o2_turnover_replay.py`: exactly 100x filled volume within two orders, equal NAV/drawdown/loss/subsidy; not an order-rate benchmark |
| A30 | `test_stress.py`, `test_spot_risk_binding.py`, `test_o2_integrated_replay.py`; derivatives extension remains O.7 |
| A35–A36 | `test_o2_revocation_replay.py`, `test_protected_okx_send.py`, `test_final_quote_send.py`, `test_request_budget.py`, `test_safety_watchdog.py`, `test_o2_integrated_replay.py` |

Remaining D/R qualification owners: connector/operator owner for actual exchange/account behavior and source completeness; risk/project owner for independent LIFE valuation and numeric fee/exit/stress/recovery budgets. The separate O.4 recorder interruption, O.5 adversarial telemetry, O.6 full acceptance/coverage review and O.7 SWAP/inventory milestones remain open. Post-only exits can remain unfilled; this scope supplies explicit residual-risk reporting, not guaranteed liquidation. Host-local journals and account locks do not coordinate other hosts or external API clients.

## O.5 spot offline closure

Reviewed by Codex on 2026-10-09 against P8.7–P8.13 and spot A04,A27–A30,A38. This is code/test review of synthetic contracts; O.6 still owns the complete acceptance matrix, changed-line coverage and broader review. Production order permission remains disabled. The telemetry adapter is opt-in and is explicitly installed by these fixtures.

### Recorded scenarios and results

The [frozen JSON bundle](evidence/life_o5_spot_replay.json) contains 14 risk scenarios, seven queue/fill scenarios at each of seeds **7** and **19**, and two net-flat turnover cases. Every trace carries scoped identities, reasons and snapshot indices. Resolve `event.snapshot` through `snapshots`; resolve its `metrics` index through `metric_snapshots`. Only the random session and wire identifiers are normalized, preserving equality within each case. The bundle records policies, cost rates, reference-model inputs, clock domains, queue assumptions and external-print inputs. `test_frozen_evidence_bundle_matches_fresh_replay` regenerates and compares the complete normalized report.

| Requirement / cases | Executable evidence and outcome | Later proof / owner |
| --- | --- | --- |
| P8.7/P8.8 | `test_telemetry.py`, `test_controller_telemetry.py`, `test_o5_system_scenarios.py`: fixed units/quality, scoped versioned identities, captured gate/quote/WAL inputs, reconstructed permission results, actual ACK/cancel/account-proof hooks; invalid, stale-writer, truncated and capacity failures deny further risk | D/R operator: deployment, retention and flush scheduling; O.7 SWAP metrics |
| P8.9 / A04 | `empty_book`, `stale_book`, `disconnect`, `resync`, `benchmark_spike`, `benchmark_stale`, `transient_depth`, `latency_spike`, `fee_regime`, `rate_limit`, `cancel_timeout`, `delayed_cancel`, `expiry_disconnected`, `crash_restart`; actual runner/final-send and reservation/reconciliation boundaries, complemented by the O.4 process/combined recovery tests | D/R connector/market-data owners: observed exchange behavior and source qualification; O.7 divergence/recovery |
| P8.10 / A04 | Each seed runs `no_trades`, `candle_touch`, `unqualified_print`, `delayed_ack`, `one_sided`, `cancel_fill_race`, `slow_adverse`. Only explicit synthetic external prints after ACK, after queue-ahead consumption and before cancellation confirmation may fill. Accepted quantities enter the real fill/fee/attribution ledgers; no-print cases have zero fills | R market-data owner: empirical queue/latency calibration |
| P8.11 | Final-check blocking at event +3 ms, explicit cancellation request at +9 ms, terminal proof at +40 ms against synthetic 5/10/50 ms budgets. The delayed case confirms at +60 ms and breaches its budget. Timeout, disconnected expiry and crash leave proof unknown; rate-limit never-sent rejection needs no cancellation. Stale BTC can fall back to independently qualified LIFE with zero influence, so it has no invented block/cancel delta | D/R risk/operator: actual numeric budgets and deployed timings |
| P8.12 / A27,A28,A30 | Proposed candidate gross/net edge uses its exact plan snapshot and maker, exit, impact, carry, inventory and uncertainty costs. Changed inputs make old-plan cost telemetry unavailable. Actual converted fill fees, independent fill-time edge, cashflow-adjusted NAV, starting inventory PnL, persisted high-water drawdown, markouts, pending exposure and budgets remain separate. Physical spot liquidation estimate is USDT cash plus depth-qualified stressed LIFE sale proceeds after exit fee | R market-data/account/risk owners: actual independent values, depth, account fees and budgets; O.7 hedge/funding attribution |
| P8.13 / A27–A30,A38 | Slow adverse fills, cancellation-window fills, withdrawn depth, latency and fee changes, net-flat loss and restart retention. One-versus-100 matched fill pairs produce **100x volume**, equal adjusted NAV **19.99 USDT**, drawdown **5 bps**, execution loss **0.01 USDT**, and settled service subsidy **0.01 USDT**. The separate turnover fixture uses a synthetic **4 bps** HALT threshold | O.7 funding/basis; R/C risk/project owners: empirical evaluation and release decision |

**Timing and quality limits.** Event timing is driven by explicit frozen monotonic clocks. Cancellation is requested by the replay driver at the recorded boundary; these deltas do not establish a production scheduler bound or measured OKX latency. A cancellation ACK remains insufficient terminal proof. ACK journal depth is labeled as a journal observation, not independently executable liquidity. The replay has no qualified two-sided availability window, so availability remains null/insufficient evidence; duration-weighted full/eligible arithmetic including pauses is tested separately.

**Economic limits.** The queue/telemetry fixture opens with synthetic 10 LIFE and 10 USDT at 1 USDT/LIFE. Maker cost is 0.0008, exit fee 0.001, impact/carry/inventory risk are 0.0001 USDT each per candidate, and uncertainty is 1 bp. Repeated fills use independent synthetic fill-time value 0.9 and horizon value 0.8 USDT/LIFE; no-trade inventory can lose value without inventing fills. The turnover fixture separately consumes explicit synthetic exchange history rather than queue-derived fills. Seeds 7/19 distinguish development/evaluation fixtures; they are not empirical calibration or out-of-sample LIFE data. The report states `INSUFFICIENT_EVIDENCE_UNCALIBRATED_SIMULATION`; passing this gate does not establish profitability, service KPIs, affordable live subsidy, or eligibility to scale capital.

**PnL reconciliation.** Total spot inventory contribution equals independently marked, cashflow-adjusted NAV change minus net fill-time edge. Its starting-inventory portion is shown separately; the remainder is explicitly a derived nonstarting-inventory residual (including post-fill/transfer price movement), not a second independently observed PnL stream. Tests verify this residual against filled quantity times the subsequent independent price change and reconcile the components to NAV. Markouts remain separate diagnostic horizons.

**Diagnostics and cancellation.** JSONL records are bounded, whitelist payload fields and exclude raw configuration, auth and exception payloads. Capture is buffered; cancellation, ACK and terminal-proof hooks only capture memory state and do not sample financial journals. Flush serializes disk work outside the capture lock. A stalled or failed telemetry flush cannot obstruct cancellation. Once detected, telemetry failure revokes new risk; the existing WAL/reservation/account proof remains authoritative. A process crash can lose unflushed diagnostic rows. This is not a second financial WAL or proof of exchange history.

### Red → Green and reproduction

The initial telemetry tests failed because the recorder module was absent. Integrated tests then exposed the wrong stress-observation age attribute and cumulative-fill input handling in the harness; the complete costs/markouts/partial-fill checks now pass. Broader regression caught a telemetry hook dereferencing the config of an intentionally malformed queued action; it now tolerates the missing ID while the existing rejection path runs. Cost telemetry additionally binds each plan to its original snapshot so a newly qualified cost update cannot be mixed with old candidate economics. A dedicated threaded test proves capture continues while flush is held at disk fsync.

Reproduce without credentials or network calls:

```sh
conda run -n hummingbot --no-capture-output python -m test.hummingbot.strategy_v2.life_liquidity.o5_scenarios \
  --output /tmp/life_o5_spot_replay.json
cmp docs/plans/evidence/life_o5_spot_replay.json /tmp/life_o5_spot_replay.json
conda run -n hummingbot --no-capture-output python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_telemetry.py \
  test/hummingbot/strategy_v2/life_liquidity/test_controller_telemetry.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o5_system_scenarios.py
```

Final O.5 closure regression: **1,277 passed, 27 warnings** across LIFE, V2 strategy base, OKX spot, OKX perpetual, executor orchestrator and MarketsRecorder (the six-suite command under O.4 below). This includes **45 new tests**. Pre-commit checks pass. Changed-line coverage is not rerun for O.5; O.6 remains open. SWAP funding/basis/hedge and inventory-execution extensions remain O.7.

## O.4 spot offline closure

Reviewed by Codex on 2026-10-09 against P8.1–P8.4 and spot A03, A14, A18, A35 and A39. This is repository/test review; deployment and real-account release remain D/R. **1,232 tests passed, 28 warnings** across the six suites below; all pre-commit checks pass. Changed-line coverage was not rerun; O.6 remains open.

### Red → Green and interruption boundaries

- The new rearm acceptance initially failed because a controller-bound gate accepted caller-supplied `reconciled=True`. The controller now installs a one-call reconciliation authority. `await controller.manual_rearm(operator_id="...")` performs fresh UID, account-wide orders/algos/fills/bills, balance, runner/recorder and journal checks under the held account lock. Every session scope must have terminal proof. The method has an explicit reconciliation timeout and rejects changed identity/configuration, lost ownership, stop/HALT, expiry/rollback and stale evidence. Cancellation retains the manual latch. Success resets observation hysteresis without resuming the session or enabling trading. A persisted HALT remains latched.
- The 22 rearm cases include a successful verified path plus live/foreign activity, missing bill anchor, balance mismatch, recorder failure, missing/corrupt action state, deleted reservation state, changed WAL, HALT, expiry/rollback, stale or stopped fetch, HALT/config/ownership changes during fetch, timeout and cancellation. Standalone safety-unit tests retain their isolated primitive contract; installed controller gates cannot use caller attestation.
- Eight killed-child recorder cases cover before/after executor INSERT, before/after its commit, before/after UPDATE and before/after its commit. Reopened SQLite rows are absent, old or updated exactly as the boundary requires. Both existing stored provenance and missing-row pre-send provenance retain live exposure; only later complete terminal exchange/account proof releases it. No restart sends another order.
- The 16-case runner matrix combines action dispatch before write/after atomic replacement, recorder before/after commit, cashflow attribution before write/after replacement, and action retirement before write/after replacement. A recorder exception retains its in-memory executor even when the row committed. Restored action/WAL/reservation/recorder evidence retains the unknown send; a foreign algo blocks terminal release. The approved +2 USDT transfer replays once, and action retirement occurs only after both WAL and reservation terminal proof. The final restore has exactly 12 USDT and one original send.
- Combined acceptance runs real spot listing/book/continuity and runtime safety gates before the original protected send; only the production release switch is replaced. Manual-rearm tests isolate recovery/account proof. Separate pre-existing tests cover process termination at WAL/action/reservation/cashflow/HALT boundaries, failed fill checkpoints, timed-out cancellation, queued/final-send revocation, corrupt storage and unchanged absolute session deadlines.

### Requirement-to-evidence map

All test names below are under `test/hummingbot/strategy_v2/life_liquidity/`.

| Requirement / cases | Executable offline evidence | Remaining environment proof / owner |
| --- | --- | --- |
| P8.1 / A35 send/ACK/fill/cancel/replace crashes | `test_recorder_cold_restart.py`, `test_o4_commit_matrix.py`, `test_o4_cashflow_runner_replay.py`, `test_crash_recovery_replay.py`, `test_wal.py`, `test_executor_protected_send.py`, `test_o2_revocation_replay.py` | R/connector/operator: deployed DB/account and real exchange history; SWAP recovery O.7 |
| P8.2 / A03 corrupt state and clock/deadline faults | `test_controller_order_safety.py`, `test_reservation_persistence.py`, `test_quote_action_recovery.py`, `test_risk_priority.py`, `test_session.py`, `test_spot_session_replay.py`, `test_o4_manual_rearm.py` | R/operator: deployed storage/clock monitoring; physical power loss is an operating assumption |
| P8.3 / A39 one host/account ownership and foreign activity | `test_account_lock.py`, `test_controller_order_safety.py`, `test_account_bills.py`, `test_order_gateway.py`, `test_o4_manual_rearm.py` | R/operator: actual dedicated UID/host/API clients, history coverage; cross-host authority outside MVP |
| P8.4 / A14 manual stop, reload and verified rearm | `test_runtime_risk_binding.py`, `test_risk_priority.py`, `test_o4_manual_rearm.py`, `test_session.py` | D/R/connector/operator: deployed stop/reload/rearm procedure |
| A18 atomic/rejected configuration | `test_config_update.py`, `test_controller_config_adapter.py`, `test_o2_revocation_replay.py`, changed-config-during-fetch case in `test_o4_manual_rearm.py` | D/R/operator: approved immutable production configuration |

Reproduce the closure regression without credentials:

```bash
conda run -n hummingbot --no-capture-output python -m pytest -q --disable-warnings \
  test/hummingbot/strategy_v2/life_liquidity \
  test/hummingbot/strategy/test_strategy_v2_base.py \
  test/hummingbot/connector/exchange/okx \
  test/hummingbot/connector/derivative/okx_perpetual \
  test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py \
  test/hummingbot/connector/test_markets_recorder.py
```

Process termination and injected exceptions do not establish physical power-loss durability. Fake authenticated responses do not establish production history completeness. P8.5–P8.6 / A13 exchange cancellation timer verification remains D/R, owned by the connector/operator. O.5 telemetry/adversarial simulation, O.6 broad acceptance/coverage and O.7 SWAP/inventory remain open. Production order permission stays disabled.

The checkpoint narratives below retain historical verdicts and counts; the current scope table and closure sections above are authoritative.

## Reproducible test and coverage commands

Run in the prepared `hummingbot` Conda environment, without exchange credentials:

```bash
conda run -n hummingbot --no-capture-output python -m pytest -q --disable-warnings \
  test/hummingbot/strategy_v2/life_liquidity \
  test/hummingbot/strategy/test_strategy_v2_base.py \
  test/hummingbot/connector/exchange/okx \
  test/hummingbot/connector/derivative/okx_perpetual \
  test/hummingbot/strategy_v2/executors/test_executor_orchestrator.py
```

The final 2026-10-09 run at commit `8525624c0` passed **1,066 tests** with
27 warnings. Python was 3.13.15 on Darwin arm64.

A later 2026-10-09 working-tree run of the same five suites after the O.2
fee/service replay extension passed **1,070 tests** with 27 warnings. The new
replay uses `LifeLiquidityController.allow_create_executor_actions()` and the
actual V2 action filter and executor send, while its synthetic harness
substitutes the spot-feed readiness check. The queued service action is
rejected after an external durable subsidy reservation changes the available
budget; no fake OKX request is sent. Another service path sends a quote, then
attributes a partial fill and USDT fee to execution loss and subsidy usage,
rejects the obsolete subsidy snapshot, and blocks an executor resend under a
latched drawdown HALT. A profit-mode path revokes a protected final send after
the account fee rises. These are deterministic contract replays, not actual
account fee, fill, value, or latency evidence.

The same five suites still passed **1,070 tests** after the O.3 partial-fill
expiry/restart extension (28 warnings). In this replay the fake account's
missing USDT fee keeps the old order unresolved after cancel; supplying the
fee and matching balance permits terminal reconciliation. Reopened WAL,
reservation, and session journals preserve the fee, trade ID, residual
balances, and expired state. The lost-ACK replacement proof remains a separate
path, so this is not the full O.3 acceptance session.

A subsequent O.3 successor replay first failed with the controller left in
`TRANSITIONING` after the old filled order and account had reconciled: its
asynchronous transition passed `market_reference_ready=False` unconditionally.
The controller now accepts an explicit finite market anchor from a
planner-validated quote snapshot only after all risk/readiness gates and scoped
order checks pass. The replay verifies that a foreign pending algo order,
an unowned completed fill, absent/non-finite anchor, and unready market
reference cannot activate a successor. A synthetic 1.02 USDT
market anchor distinct from the old 1.00 USDT quote reference activates the
persisted successor, whose new quote has another intent and wire ID. This
does not establish a production-quality independent market source, full
account history, or live order eligibility.

After the account-wide completed-fill scope was added to that replay, the
same five-suite regression passed **1,071 tests** with 27 warnings. The
changed-line coverage figure below predates this extension.

The async V2 listener then failed a cross-batch queue replay: it dispatched
an earlier LIFE create before a LIFE stop already waiting in the next batch.
It now checks pending batches for LIFE stops before dispatching any of them,
rejects the conflicting create claim, and still dispatches the stop if claim
rejection fails or an earlier batch is malformed. The five-suite regression
passed **1,075 tests** with 27 warnings after this change. This guarantee
covers batches already queued when the listener wakes; a stop arriving after
a create was dispatched depends on the separate safety/final-send gates.

The subsequent O.3/O.4 slice rejects a successor anchor that differs from the
fresh own-depth-separated LIFE book in the combined runner path. That replay
refuses missing and foreign regular orders, a foreign algo order, an unavailable
second page of account-wide fill history, and a foreign completed fill. It
obtains a status-backed ACK for the successor after the old quote's lost ACK,
fill, fee, and expiry. This closes O.3 for synthetic spot offline scope; the
production trading switch and spot readiness remain deliberately disabled and
the injected fake book is not live LIFE market evidence. A separate runner fault replay retains the
reservation after a timed-out cancel, then releases it only after terminal
exchange/account proof; a clock rollback pauses the session without extending
its deadline. The manual HALT API persists a latch, rejects an already queued
create, and schedules cancellation. A checkpoint error still blocks the
current process, but restart after unavailable storage requires separate
operational proof. These additions do not close O.2 or O.4.

The final five-suite run for this checkpoint passed **1,082 tests** with 27
warnings. Changed-line coverage was not rerun after this extension; the 88%
figure below remains a prior checkpoint, and O.6 is still open.

A subsequent working-tree run passed **1,092 tests** with 28 warnings after
independently valued LIFE-fee accounting, a fake OKX LIFE-fee gateway replay,
and safety-journal fail-closed checks, including a stale gate after another
gate persists HALT.
The safety tests first failed on an unavailable strict-recovery API and on a
drawdown checkpoint error that left the gate in `NORMAL`; both now pass.
The fee tests first failed because attribution accepted only USDT fees.
This remains an O.2/O.4 checkpoint, not acceptance of either gate.

The next five-suite run passed **1,094 tests** with 27 warnings after adding
two V2/fake OKX cashflow interruption replays. One fails before the attribution
journal write; the other fails after replacement. Restoring the journals
attributes the approved transfer once, while the original live order remains
unknown/reconciling with its reservation and no second send.

The latest five-suite run passed **1,096 tests** with 27 warnings after a
recovered clear safety journal was made to require explicit operator rearm.
The test injects a HALT write failure before replacement, restores the old
clear journal, and confirms the gate cannot resume from healthy observations
alone. The current rearm API accepts a caller-attested reconciliation flag;
binding it to verified account and order evidence is still required for O.4.

The subsequent O.2 checkpoint passes **1,126 tests** with 27 warnings across
the same five suites. The integrated replay now evaluates the real listing,
continuous-trading, snapshot-age, local book and WS continuity gates against
synthetic observations. It replaces only the production release switch in the
test harness; production LIFE order permission remains disabled. Sixteen new
cases reject actual V2 queued actions or final-wire/executor retry attempts after
disconnection, resync, stale book/risk observations, HALT, rejected config
changes, quote expiry or session expiry. A healthy unknown send also remains
ineligible for a blind retry.

A controller settlement API requeries exchange/account/runner scope before
releasing unused service holds for an explicitly selected group of terminal,
fully attributed intents with exactly zero net physical LIFE flow. LIFE fees
are included in that flow. Settlement charges the larger of realized USDT cash
loss and the existing independent per-fill loss floors, preserving conservative
charges even when other fills gain. Additional inventory loss belongs to the
closing intent; each subsidy entry retains its reservation UTC day/session,
while execution-loss events retain their fill UTC day. One atomic journal commit
records all members and a fill/identity hash that attribution recomputes after
restart. Unknown account scope, residual inventory, reused members, changed
proof/cost, wrong session identity and uncertain writes cannot release capacity.
Lot selection is explicit; no automatic matching or exit execution is supplied.

The A29 runner replay sends two real `OrderExecutor` requests to fake OKX and
reconciles one versus 100 matched partial-fill pairs of equal size. Filled volume
increases exactly 100 times, while both runs finish at synthetic adjusted NAV
19.99 USDT, 5 bps drawdown, 0.01 USDT execution loss and 0.01 USDT settled
subsidy. A synthetic 4 bps drawdown threshold latches HALT in both runs. This
proves the accounting invariant for those inputs, not 200 sequential order
placements, order-rate performance, or profitability.

**Historical checkpoint before the closure above: O.2 remained open.** Its remaining offline gates were:
durable rolling-window replenishment, reservation-derived stress at final send,
bounded exit routing/budgets, bounded degraded recovery, runtime fee refresh and
independently valued LIFE transfers, and combined cancellation-load acceptance.
Qualified live observations/calibration remain separate R evidence. Changed-line
coverage was not rerun for this checkpoint.

Coverage used the same regression scope at commit `8525624c0` and `origin/dev`
as the available local comparison branch. `origin/development` is absent.
NumPy and Pandas were
imported before enabling coverage tracing because tracing their first import
triggered a local Python 3.13 extension import error. `diff-cover` reported
**88%** on **1,478 changed lines**, with **176 missed**; the directly changed
controller module was included. This clears the numeric 80% diff threshold for
this checkpoint, but says nothing about the missing scenario acceptance.
Coverage XML was written to `/tmp/life_offline_coverage_final.xml`; that path is
ephemeral and must be regenerated before review.

## Red → Green examples

| Test | Failure before change | Current result |
| --- | --- | --- |
| `test_hedge.py::test_zero_residual_does_not_create_an_urgent_hedge_after_elapsed_deadline` | `HEDGE_DEPTH_INSUFFICIENT` for exact zero delta after timer | `HEDGE_BALANCED`, no order |
| `test_queue_latency_simulation.py::test_unattributed_print_is_unqualified_by_default` | Default `TRADE` filled all 3 LIFE | Zero fill, `PRINT_UNQUALIFIED` |
| `test_subsidy_runtime_binding.py::test_zero_fill_terminal_releases_hold_only_after_both_journals_agree` | No durable terminal settlement API | Hold released only after both restored journals agree |
| `test_inventory_execution.py::test_fill_price_fee_and_fixed_benchmark_bound_actual_exit_budget` | No explicit fee or shortfall journal entry | Fee/shortfall preserved across restart and further children blocked at budget |
| `test_reconciled_fill_attribution.py::test_life_fee_uses_independent_fill_value_and_replays_physical_balance` | LIFE fee could not enter attributed capital/loss | Physical LIFE fee and independent quote cost replay after restart |
| `test_risk_priority.py::test_halt_checkpoint_failure_latches_memory_and_revokes_permission` | Failed HALT checkpoint left `NORMAL` in memory | Current process remains HALTED and refuses quotes |
| `test_inventory_subsidy_settlement.py::test_flat_cycle_settles_realized_inventory_loss_and_replays_once` | No closed-inventory settlement API | Net-flat, reconciled cycles settle once; residual inventory retains its hold |
| `test_inventory_subsidy_settlement.py::test_inventory_settlement_cannot_charge_a_different_session_budget` | A restored subsidy entry with an unrelated session still passed attribution readiness | Mismatched session blocks attribution and settlement |

## Replay and economic limits

The queue fixture in `test_queue_latency_simulation.py` uses seed 7, one LIFE
lot ahead in its main case, synthetic 100 ms ACK and 200 ms cancellation
latencies, and a synthetic 0.1% maker-fee assumption. It records inputs and
per-event reason codes. These are assumed model parameters; the result is a
**simulated fill**, not an observed OKX fill, queue position, or liquidity KPI.
The inventory benchmark is fixed before its session and its fees are explicit,
but its journal still lacks authenticated fill and cancel evidence. Neither
fixture can establish profitability or a live subsidy budget.

## Classification of remaining detailed items

Mixed-stage parent checkboxes remain open for their explicitly classified D/R/O.7 proof. O.2–O.5 spot offline acceptance is complete as mapped above; its remaining production inputs do not reopen the offline gate.

| Detailed items | Offline gate | Later proof |
| --- | --- | --- |
| P4.1–P4.18 | O.2; spot risk/economics queue, final send and replay | D runner/connector behavior; R qualified LIFE value, fee, capital and budgets |
| P5.1–P5.13 (including checked P5.12) | O.3; spot quote/gateway/account-scope lifecycle | D exchange behavior; R LIFE book, actual account/history and calibrations |
| P6.1–P6.15 | O.7; SWAP fake-contract, order/recovery and joint-risk acceptance | Separate D and R for enabled SWAP; C before capital allocation |
| P7.1–P7.8 | O.7; inventory route, fill proof and mode transition | Separate D and R for enabled inventory execution; C before capital allocation |
| P8.1–P8.4 | O.4; fault/restart/kill-switch replay | R account ownership and complete exchange history |
| P8.5–P8.6 | No O requirement for the actual exchange timer | D/R supported-account timer and cancellation scope |
| P8.7–P8.13 | O.5; telemetry and adversarial scenarios | D latency behavior; R LIFE calibration; C economic release decision |
| Spot A01–A39 applicability, regression and review | O.6 | D/R/C only for cases explicitly requiring those environments |

The next spot offline priority is the O.6 acceptance/coverage review; O.5 telemetry/adversarial replay is complete. SWAP send/recovery and inventory runner routes remain the largest O.7 gaps. Missing evidence must leave its checkbox open.
