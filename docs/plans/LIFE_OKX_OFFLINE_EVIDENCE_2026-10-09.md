# LIFE/OKX offline evidence checkpoint — 2026-10-09

## Scope and verdict

This checkpoint covers synthetic LIFE spot scenarios, selected joint spot/SWAP
risk contracts, and an advisory inventory execution journal. It closes the
**spot offline O.3 lifecycle replay**, while O.2 and O.4–O.7 remain open in
the [implementation plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).
No demo or production order was sent. All quoted balances, limits, prices,
fees, latencies, and queue assumptions in these tests are simulation values;
none are live defaults.

| Gate | Current evidence | Still needed offline |
| --- | --- | --- |
| O.2 spot economics/risk | Actual V2 runner/fake OKX replay covers safety recovery, fee repricing at final send, queued service-action rejection after a subsidy change, reconciled partial fill and USDT fee, subsidy floor, stale snapshot, HALT and journal restore; proven zero-fill hold release; LIFE stop priority across batches already queued; manual HALT rejects a queued create. A separate fake OKX gateway replay reconciles a LIFE-denominated fee using independent fill-time valuation into physical balances, loss, and subsidy | Complete spot-feed readiness and account-data binding, filled-inventory settlement, 100x-turnover, late-arriving stop/cancellation stress and remaining A-cases |
| O.3 spot lifecycle | **Offline complete.** One real V2 runner/fake OKX session covers lost ACK, partial fill/fee, expiry/cancel, missing/foreign regular orders, foreign algo order, incomplete paginated and foreign completed-fill history, own-depth-qualified successor anchor, replacement quote with status-backed ACK, residual inventory, and journal restore | Demo connector behavior and real-account history/reference qualification remain D/R work; production order permission stays disabled |
| O.4 recovery | Existing process-kill/SQLite/WAL/reservation slices; runner cancel timeout and clock rollback; manual HALT persistence, queue rejection, and cancellation scheduling. Missing/corrupt safety journal blocks recovery; deletion after quote approval revokes final send while retaining its reservation; HALT latches in memory before a failing checkpoint. A recovered clear journal requires manual rearm, including when HALT failed before its write. A V2/fake OKX send remains unknown and reserved after cashflow attribution fails before write or after replacement, then replays once on restart | Remaining combined recorder/quote-action/cashflow interruption matrix and binding manual rearm to authoritative account reconciliation |
| O.5 simulation/telemetry | Seeded queue-ahead and ACK/cancel latency fixture; candle touch and unattributed prints make no fills | Recorded adversarial scenario suite, complete quality-flagged telemetry, risk-event latency budgets and economic evaluation |
| O.6 review | Scoped regression and changed-code coverage below | Full spot A01–A39 matrix, changed-code review, named reviewer and reproducible evidence bundle |
| O.7 SWAP/inventory | Joint pending-fill stress, conservative hedge decision, inventory child/exit-cost journal | Protected SWAP order gateway, authenticated fills/funding/margin, shared coordinator, inventory runner route and mode transitions |

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

**O.2 remains open.** The plan now lists its remaining offline gates explicitly:
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

The parent checkboxes remain open until their entire offline acceptance passes.
Some contain additional demo/real-account proof; that proof stays open after O.

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

The next offline priority is O.2 spot economic binding and the remaining O.4
crash boundaries; the SWAP send/recovery and inventory runner routes are
the largest O.7 gaps. Missing evidence must leave its checkbox open.
