# LIFE/OKX offline evidence checkpoint — 2026-10-09

## Scope and verdict

This checkpoint covers synthetic LIFE spot scenarios, selected joint spot/SWAP
risk contracts, and an advisory inventory execution journal. It does **not**
close O.2–O.7 in the [implementation plan](LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md).
No demo or production order was sent. All quoted balances, limits, prices,
fees, latencies, and queue assumptions in these tests are simulation values;
none are live defaults.

| Gate | Current evidence | Still needed offline |
| --- | --- | --- |
| O.2 spot economics/risk | Protected service quote, reconciled USDT-fee fill loss and subsidy floor, journal restart, proven zero-fill hold release | Full unmodified V2 replay, authenticated fee/valuation source binding, filled-inventory settlement, 100x-turnover and all A-cases |
| O.3 spot lifecycle | Real V2 runner lost-ACK quote, terminal proof and new wire ID; separate partial-fill/expiry and cold-restart replays | One complete quote → fill/fee → replace → expiry/successor → restart replay with all account-wide scopes |
| O.4 recovery | Existing process-kill/SQLite/WAL/reservation slices | Remaining recorder, quote-action, cashflow, kill-switch and clock fault matrix |
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

The next offline priority is a complete O.3 runner session and its remaining
O.4 crash boundaries; the SWAP send/recovery and inventory runner routes are
the largest O.7 gaps. Missing evidence must leave its checkbox open.
