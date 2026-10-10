# LIFE/OKX O.6 spot offline acceptance — 2026-10-10

## Verdict and declared scope

**Spot Offline Complete: O.1–O.6. Full offline scope is not complete: O.7/P6/P7 remains open.**

Reviewed by **Codex, 2026-10-10**, against the repository implementation,
actual executed test results, frozen O.5 replay and per-stage acceptance matrix.
This is a software evidence review; operator approval and economic release are
separate R/C decisions. No credentials, demo orders or production orders were used.
The controller's production permission remains disabled.

The declared scope is one LIFE-USDT spot market, a single-source optional
research benchmark, explicitly installed offline adapters, actual V2 queue and
OrderExecutor interfaces, fake OKX authenticated responses and synthetic clocks,
values, budgets, fees, depth, external prints and latency assumptions. These are
not live defaults. Production input acquisition/installation is still D/R work.

- **29 A-cases have verified offline spot portions**; their later-stage portions remain open.
- **10 A-cases are deferred, not passed:** A10, A12, A13, A16, A17, A31–A34, A37.
- **118 unchecked detailed P requirements** (78 parents and 40 children) are classified individually with exact text fingerprints, owners, evidence and remaining stages in the manifest.
- **14 live decisions** in plan section 9 remain R/C, including actual instrument/rules, independent valuation, benchmark eligibility, session policy, capital/exposure, objective/KPIs/budgets, costs/flows, account/margin/hedge policy, response latency, markout/stress/headroom, inventory execution, account scope, operators/rollback and canary criteria. Their owners are the account operator and project/risk owner; missing numbers never become simulation defaults.

### Completion evidence

| Gate | Result |
| --- | --- |
| Affected regression | **1,547 passed; 0 skipped; 0 failed; 27 warnings** |
| Full-feature changed-line coverage | **89.40%**: 9,340 executable changed lines, 990 missed; 80% gate passed |
| Since pinned O.2 changed-line coverage | **92.75%**: 483 executable changed lines, 35 missed; 80% gate passed |
| Changed production source inclusion | All **59** changed Python source files measured; no silently omitted source file |
| Acceptance evidence | Every referenced selector collected and every parameter variant completed setup, call and teardown successfully |
| Deferred requirement audit | All 118 exact unchecked P items classified; missing/stale/unowned entries fail the audit |
| Replay | The O.5 frozen bundle is regenerated and compared within the regression |
| Release | Spot software contracts only; no demo proof, real economic evidence or live permission |

The committed [manifest](evidence/life_o6_acceptance_manifest.json) is the reviewed
mapping. The [measured summary](evidence/life_o6_spot_acceptance.json) records
actual test counts, matched-node fingerprints, exact coverage bases, per-file
missed lines, runtime/tool versions and the tested HEAD plus worktree status.
Counts and coverage in older evidence files are historical checkpoints.

## Reproduce the complete gate

Run from the repository root in the installed Hummingbot source environment:

```bash
conda run -n hummingbot --no-capture-output python -m \
  test.hummingbot.strategy_v2.life_liquidity.o6_acceptance \
  --output-dir /tmp/life-o6
```

The command runs the manifest's full regression scope, captures every collected
node and setup/call/teardown outcome, checks all acceptance and detailed-item
references, generates coverage XML, enforces the 80% changed-line gate for both
pinned bases and writes `summary.json` only after every check succeeds. It
removes an old summary before starting, so a failed rerun cannot leave a current
success result. XML, detailed test outcomes and raw diff reports are ephemeral
outputs that can be regenerated; the compact summary is retained in this repo.

Regression scope: LIFE package; V2 strategy base; OKX spot and perpetual;
executor orchestrator, OrderExecutor and TWAPExecutor; V2 controllers;
MarketsRecorder; MarketDataProvider; and REST/WS web-assistant tests. This expands
O.5's six-suite scope to the adjacent interfaces changed by the feature. It is
not a claim that every unrelated connector in the repository was tested or that
remote CI passed. No skipped test is accepted as evidence.

Coverage sources explicitly include `hummingbot` **and** `controllers`.
Repository `.coveragerc` branch/exclusion settings are retained. NumPy/Pandas
load before tracing because this local Python 3.13 environment fails when their
first extension import is traced. Coverage does not replace scenario review.

Pinned comparison bases:

- **Full LIFE feature:** `34432aadf809f3da4728f8d3117b0591fd132bdd`, parent of the first LIFE implementation commit.
- **Recent O.4/O.5 changes:** `4efb4ad6e0a595118e58c2d38b97f993d36ea41c`, the locally available `origin/dev` SHA at review time.

`origin/development` is absent locally. The full-feature base prevents a moved
`origin/dev` from hiding earlier changes. Neither pinned local result proves
coverage against an unknown future PR target; CI must repeat its own base gate.
The percentage denominator is executable changed source lines, not generated
JSON, documentation or test-file lines. Remaining missed lines are retained per
file rather than suppressed to improve the percentage.

## TDD and review findings

| Evidence | Red / gap | Green |
| --- | --- | --- |
| `test_o6_acceptance.py::test_unknown_deferral_stage_cannot_close_spot_gate` | New audit initially accepted `demo-later` as a deferral stage; focused run: **1 failed, 7 passed** | Explicit stage whitelist rejects the invalid classification; focused run: **8 passed** |
| Remaining audit guard cases | Missing selector, absent call, skipped setup/call, failed teardown, unclassified plan item and unexecuted detailed-item reference must not count as evidence | Every variant fails the audit as required; successful parameter siblings cannot hide a failure |
| `test_session.py::test_configured_duration_survives_restart_and_expires_at_exact_boundary` | A02 lacked an explicit combined 30m/4h/12h restart/boundary table | Three added cases retain the original anchor and expire at the exact configured deadline; existing implementation already satisfied them, so no fabricated Red is claimed |
| Complete audit invocation | First end-to-end attempt passed 1,539 tests but failed at the diff-cover API's argv convention | Corrected invocation includes the program argument; the final complete gate, including eight audit tests, passes |
| Existing strategy TDD | Earlier O.2–O.5 Red → Green findings include loss/subsidy settlement, LIFE fee attribution, markout revocation, pause reconciliation, killed recorder boundaries and authoritative manual rearm | Preserved in the [2026-10-09 historical evidence](LIFE_OKX_OFFLINE_EVIDENCE_2026-10-09.md) and rerun by this gate |

Source review checked the controller's default-deny production permission,
WAL/reservation identity before send, final authorization **after** OKX throttling,
unknown-send retention and separation of ACK from complete terminal account proof.
It also checked telemetry's memory-only cancellation hooks, diagnostic failures
that cannot discard ACKs, exact plan-snapshot cost identity, missing financial
values and distinct NAV/markout/loss accounting. Existing regression exercises
those boundaries. Coverage misses include defensive malformed-state/error guards,
storage failure variants and SWAP-side paths; the per-file report preserves them.
No new spot send-path defect was found in this review. This is not formal
verification of all interleavings or a guarantee of bounded market loss.

## A01–A39 stage-aware evidence

File names below are executable pytest selectors under
`test/hummingbot/strategy_v2/life_liquidity/`; the manifest contains full paths
and selected function IDs. “Verified” means only the explicitly described O
portion. All synthetic observations remain unqualified for production economics.
Deferred rows have no passing O claim even where standalone connector/calculation
tests exist. Later implementation as well as later evidence is retained explicitly.

| Case | O result / tested behavior | Executable evidence | Remaining |
| --- | --- | --- | --- |
| A01 | **Verified** — Exact missing-instrument and malformed listing responses deny the real V2 queued create path. | `test_runtime_integration.py::test_controller_polling_unlisted_life_never_submits_a_queued_order` | D / R |
| A02 | **Verified** — 30m/4h/12h retain the absolute deadline and anchor across restart; permissions expire at the exact boundary. | `test_session.py::test_configured_duration_survives_restart_and_expires_at_exact_boundary` | D / R |
| A03 | **Verified** — Original and successor anchors/deadlines persist; expired downtime cannot chain successors; replacement requires full proof. | `test_session.py`, `test_reference_transition.py`, `test_spot_session_replay.py` | D / R |
| A04 | **Verified** — Quote/cancel, candle touches and unqualified prints generate no fills. Only qualified explicit external prints after ACK enter accounting. | `test_fakes.py`, `test_queue_latency_simulation.py`, `test_o5_system_scenarios.py` | D / R |
| A05 | **Verified** — Relative-return and currency conversion preserve LIFE units despite the nominal BTC price. | `test_reference.py::test_absolute_btc_price_cannot_become_life_price` | D / R |
| A06 | **Verified** — Stale/out-of-order/invalid observations and disconnected/resync feeds revoke actual queued/final sends and retain unresolved cancellation state. | `test_market_data.py`, `test_o2_revocation_replay.py`, `test_o5_system_scenarios.py` | D / R |
| A07 | **Verified** — Rolling side and campaign capacity includes pending orders, durable reconciled fills and restart history. | `test_risk.py`, `test_quote_capacity.py`, `test_spot_risk_binding.py`, `test_o2_integrated_replay.py` | D / R |
| A08 | **Verified** — Cancel ACK and duplicate/out-of-order partial fills never double-book fills or release unresolved inventory. | `test_fill_cancel_race.py`, `test_runner_fill_events.py`, `test_spot_session_replay.py`, `test_o5_system_scenarios.py` | D / R |
| A09 | **Verified** — Lost ACK keeps the same wire identity and reservation; actual executor retry cannot blindly submit a second order. | `test_spot_session_replay.py`, `test_crash_recovery_replay.py`, `test_o2_revocation_replay.py` | D / R |
| A10 | **Deferred** — Joint-risk calculation exists, but concurrent spot/SWAP runner allocation and shared account authority require O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A11 | **Verified** — A successor lacking independent two-sided LIFE evidence pauses even when generic market readiness is true. | `test_reference_transition.py`, `test_spot_session_replay.py` | D / R |
| A12 | **Deferred** — Advisory inventory journal is not a P7 child-order runner. Thin-book/deadline acceptance stays O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A13 | **Deferred** — Account/tag-scoped exchange cancellation timer and heartbeat stall integration are not implemented/proven; D must implement and test them. Generic crash/reconciliation tests do not pass this case. | Deferred; no passing spot claim | D / R |
| A14 | **Verified** — HALT/manual stop latches across reload and recovery; manual rearm requires authenticated-style full account proof and cannot clear HALT. | `test_runtime_risk_binding.py`, `test_o4_manual_rearm.py`, `test_runtime_integration.py` | D / R |
| A15 | **Verified** — Offline proof is only the nontrading configuration/runner/production permission guard. Actual real-data shadow is deferred D/R. | `test_config.py`, `test_runtime_integration.py`, `test_controller_telemetry.py` | D / R |
| A16 | **Deferred** — ONEWAY connector reduce-only contracts exist; partial-close retry/recovery through a LIFE perpetual runner remains O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A17 | **Deferred** — Basket configuration is rejected by the MVP feature gate; basket loss/reweight policy is a deferred P3 feature, not passed by single-source tests. | Deferred; no passing spot claim | O.7 |
| A18 | **Verified** — Rejected/interrupted reload preserves validated config version, latches rejection and revokes queued/final sends. | `test_config_update.py`, `test_controller_config_adapter.py`, `test_o2_revocation_replay.py`, `test_o4_manual_rearm.py` | D / R |
| A19 | **Verified** — Auction/prequote crossed books are observations without continuous-trading permission. | `test_market_data.py`, `test_runtime_integration.py`, `test_runtime_integration.py::test_live_auction_cannot_create_even_with_book_and_other_permits` | D / R |
| A20 | **Verified** — Simulated bootstrap and all live modes remain rejected; no order-sending adapter is enabled. | `test_config.py::test_synthetic_bootstrap_is_simulation_only`, `test_config.py::test_live_is_still_disabled_after_all_current_numeric_fields_are_filled` | D / R |
| A21 | **Verified** — Profit quotes require conservative positive net edge. Service holds/floors settle once within budgets; exits retain separate hard loss/slippage policy. | `test_economics.py`, `test_fee_quote_binding.py`, `test_subsidy_runtime_binding.py`, `test_inventory_subsidy_settlement.py`, `test_protected_exit.py` | O.7 / D / R |
| A22 | **Verified** — Signed actual USDT/LIFE fees and rebates, exact group/schema/identity and stale snapshots are normalized or blocked; real conversion remains R. | `test_fees.py`, `test_fee_refresh.py`, `test_fee_quote_binding.py`, `test_reconciled_fill_attribution.py` | D / R |
| A23 | **Verified** — Actual V2 queue, real executor retry and final check after throttling reject expired/HALT/stale/config permits; replacement needs fresh epoch proof. | `test_o2_revocation_replay.py`, `test_final_quote_send.py`, `test_protected_okx_send.py`, `test_spot_session_replay.py` | D / R |
| A24 | **Verified** — Safety cancellation/expiry/reconciliation run without global readiness or executor update events. Enabled SWAP interactions remain O.7. | `test_safety_callback_integration.py`, `test_safety_watchdog.py`, `test_controller_order_safety.py`, `test_o5_system_scenarios.py` | O.7 / D / R |
| A25 | **Verified** — Own ACKed/pending depth is subtracted or unavailable, tiny/transient depth cannot qualify the reference or raise risk capacity. | `test_reference.py`, `test_own_depth.py`, `test_own_depth_runner.py`, `test_quote_reference_binding.py`, `test_o5_system_scenarios.py` | D / R |
| A26 | **Verified** — Excess divergence blocks benchmark quoting; stale benchmark can only fall back to independently qualified LIFE with zero influence. | `test_reference.py`, `test_quote_reference_binding.py`, `test_o5_system_scenarios.py` | D / R |
| A27 | **Verified** — Adverse alternating/small fills consume independent execution loss and markout cohorts even with net-flat holdings. | `test_markout_runtime_binding.py`, `test_quote_capacity.py`, `test_o2_turnover_replay.py`, `test_o5_system_scenarios.py` | D / R |
| A28 | **Verified** — Day/campaign budgets, unresolved holds, HALT and hysteresis survive restart; probes have explicit bounded quantities and costs. | `test_runtime_loss_binding.py`, `test_recovery_probe_binding.py`, `test_subsidy_runtime_binding.py`, `test_o2_integrated_replay.py`, `test_o5_system_scenarios.py` | D / R |
| A29 | **Verified** — 100x filled volume leaves NAV/drawdown/loss/subsidy unchanged. Approved flows and starting-inventory returns are separate; forecast values cannot certify NAV. | `test_o2_turnover_replay.py`, `test_accounting.py`, `test_life_cashflow_value.py`, `test_capital_risk_binding.py`, `test_controller_telemetry.py` | D / R |
| A30 | **Verified** — Spot all-side pending fills and vanished independent depth enter reservation-derived stress; physical residual inventory stays visible. | `test_stress.py`, `test_spot_risk_binding.py`, `test_o2_integrated_replay.py`, `test_o5_system_scenarios.py` | O.7 / D / R |
| A31 | **Deferred** — Metadata conversion primitives and connector tests exist; full contract/fill/position lifecycle consistency remains O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A32 | **Deferred** — Exchange reduce-only connector request is covered, but shrink-between-snapshot/send LIFE runner acceptance remains O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A33 | **Deferred** — Standalone joint exposure/stress contract is insufficient for shared-collateral tier/basis shocks through an enabled SWAP runner; O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A34 | **Deferred** — Hedge decisions are advisory. Funding updates, execution churn bounds and post-expiry position monitoring remain O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A35 | **Verified** — WAL/reservation/action/SQLite boundaries recover the correct ID, retain unknown exposure and reject blind resend or executor-terminal shortcuts. | `test_wal.py`, `test_executor_protected_send.py`, `test_runner_order_scope.py`, `test_recorder_cold_restart.py`, `test_o4_commit_matrix.py`, `test_crash_recovery_replay.py` | D / R |
| A36 | **Verified** — Spot local cancellation reserve, saturated queue, stop priority and independent watchdog are verified. Exchange heartbeat failover/stall timer remains D/R, not simulated proof. | `test_request_budget.py`, `test_safety_watchdog.py`, `test_quote_action_dispatch.py`, `test_o2_integrated_replay.py`, `test_o5_system_scenarios.py` | D / R |
| A37 | **Deferred** — Participation/impact calculations exist only in an advisory inventory journal; actual P7 child-order volume-inflation acceptance remains O.7. | Deferred; no passing spot claim | O.7 / D / R |
| A38 | **Verified** — Synthetic objective gates reject unaffordable costs, sparse/overlapping evidence fails research qualification and frozen system replay reports insufficient evidence. Real economic release remains R/C. | `test_economics.py`, `test_subsidy_budget.py`, `test_reference_evaluation.py`, `test_o5_system_scenarios.py` | R / C |
| A39 | **Verified** — UID-bound same-host lock blocks another strategy ID/state directory/process; foreign account activity blocks terminal release. Multihost coordination is outside MVP; shared SWAP authority stays O.7. | `test_account_lock.py`, `test_controller_order_safety.py`, `test_account_bills.py`, `test_o4_manual_rearm.py` | O.7 / D / R |

## Open detailed requirements: ownership and next stage

The manifest fingerprints **every** unchecked P parent and child, not just the
rows summarized here. It rejects added, removed or edited unchecked requirements
until their classification is reviewed. The following table summarizes each of
the 78 parents; anonymous children inherit their named parent's evidence and
remaining-work classification, with their own exact text and fingerprint stored
individually. A parent stays unchecked when it spans later environments.

Owners are assigned by role: **strategy/connector maintainer** implements and
verifies adapters; **risk/project owner** supplies calibration, numerical budgets
and objective evidence; **account operator** owns endpoints, deployed account,
runbook and release decisions. Named people and unresolved numeric live inputs
remain required in the decision register before R/C; these role assignments do
not constitute operator approval.

| Requirement | Remaining stage | Reason / remaining work |
| --- | --- | --- |
| P4.1 | D / R | Qualified spot balances/value/budget installation and real behavior; risk bounds already pass on the fake runner. |
| P4.2 | O.7 / D / R | Same-host spot contention is proven. Shared spot/SWAP coordinator and deployed account ownership remain. |
| P4.3 | D / R | Real delayed fill/cancel event and full account proof must repeat the synthetic ledger invariants. |
| P4.4 | D / R | Verify actual authenticated history completeness/retention before releasing unknown reservations. |
| P4.5 | R | Calibrate live rolling-window and campaign capacity; durable replenishment bounds pass offline. |
| P4.6 | D / R | Install qualified account/feed/latency/model observations and verify actual cancellation. |
| P4.7 | O.7 / R | Spot independent NAV/high-water/flow binding passes. Funding attribution stays O.7; real valuation/history and numeric thresholds stay R. |
| P4.8 | O.7 / D / R | Spot HALT/rearm is proven. SWAP margin trigger binding stays O.7; deployed behavior and real thresholds stay D/R. |
| P4.9 | R | Qualify independent horizon prices, cohort sample quality and numeric live markout limits. |
| P4.10 | D / R | Repeat queue/stop/final-send priority on isolated demo and later approved production inputs. |
| P4.11 | O.7 / D / R | Spot fee/subsidy attribution and settlement pass. Hedge/carry cost binding stays O.7; qualified tiers/values/policies require D/R installation. |
| P4.12 | D / R | Install bounded spot exit policy using qualified executable depth and explicit numeric budgets; unfilled residual is reported, not guaranteed sold. |
| P4.13 | O.7 / D / R | Spot queue/retry/throttled final permits pass. SWAP bridge is O.7; production inputs and actual exchange behavior need D/R. |
| P4.14 | D / R | Spot combined persistence boundaries pass O.4; deploy WAL-first sender with verified account/live inputs. Physical power-loss behavior remains an explicit deployment assumption. |
| P4.15 | D / R | Spot independent watchdog, retry and local headroom pass. Shared account API coverage, numeric headroom and deployed timings need implementation/qualification in D/R. |
| P4.16 | O.7 / R | Spot all-side reservation stress passes. SWAP margin/basis/funding/outage runner remains O.7; depth/shock calibration remains R. |
| P4.17 | O.7 / R | Spot physical fees/flows and independent inventory/execution returns pass. Actual values/history remain R and SWAP funding remains O.7. |
| P4.18 | R | Immutable budget identity, restart, bounded probes and HALT pass; calibrate and own live policies. |
| P5.1 | D / R | Offline opt-in candidates/final gates pass. Build production quote-source installation from qualified LIFE/account/fee/depth inputs and calibrated KPIs in D/R. |
| P5.2 | D / R | LIMIT_MAKER/post_only/no blind market fallback is proven against connector contracts; repeat actual demo rejection and account behavior. |
| P5.3 | D / R | Durable level/intent/slot ownership and safe replacement pass; qualified production issuance and deployed single-host ownership remain D/R. Multihost send is prohibited. |
| P5.4 | D / R | Actual event types and fake authenticated reconciliation pass. Verify real OKX tracker/listener ordering and complete history on demo/account. |
| P5.5 | R | Inventory taper and final-send revocation pass; calibrate thresholds from funded balances and live risk limits. |
| P5.6 | D / R | Host-local durable dispatch/request budgets pass. Cover every authenticated request/client in deployment, authenticate UID and calibrate API/refresh policy in D/R. |
| P5.7 | O.7 / D / R | Spot issuance, migration/provenance, combined recovery and successor proof pass O.3/O.4. SWAP scope remains O.7; actual exchange event/history and production installation remain D/R. |
| P5.8 | D / R | Fake-exchange real-loader/runner session and independent successor proof pass. Configuration-driven qualified production reference/planner/sender installation remains D/R implementation. |
| P5.9 | D / R | Supplied adaptive fee/volatility/inventory/depth/markout contract and final revocation pass. Qualified adaptive signal acquisition/installation and calibration remain D/R implementation. |
| P5.10 | D / R | Durable cumulative fill/loss identity and crash recovery pass. Qualify live source/history and numerical limits before production installation. |
| P5.11 | D / R | Advisory refresh cost/latency sensitivity is complete offline. Automatic refresh decision-to-cancel scheduling and qualified input acquisition remain D/R implementation; no deployed adaptive refresh is claimed. |
| P5.13 | D / R | Local real tracker/public snapshot subtraction and final reference recheck pass. Authenticate complete account-wide own orders and synchronized production book/exit inputs before installation. |
| P6.1 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.2 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.3 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.4 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.5 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.6 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.7 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.8 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.9 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.10 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.11 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.12 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.13 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.14 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P6.15 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.1 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.2 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.3 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.4 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.5 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.6 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.7 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P7.8 | O.7 / D / R | Full enabled-extension fake-runner allocation/execution/recovery acceptance is not complete. Standalone or connector slices are not a runner pass; implement O.7 before demo, then qualify actual account/market/cost inputs. |
| P8.1 | O.7 / D / R | Spot recorder/WAL/action/fill/cashflow interruption acceptance is complete O.4. SWAP remains O.7; deployed storage and actual account proof remain D/R. |
| P8.2 | D / R | Failed/corrupt/stale storage and clock rollback pass offline. Verify deployed filesystem and timing assumptions; process kill is not physical power-loss proof. |
| P8.3 | O.7 / R | Single-host spot UID lock and foreign-activity detection pass; SWAP shared sender remains O.7. Verify dedicated real UID/host/API-client ownership; no active-active deployment. |
| P8.4 | D / R | HALT/expired/rejected reload cannot resume spot; repeat with deployed loader and approved configuration. |
| P8.5 | D / R | Exchange timer/heartbeat scope and stalled-loop authority are not implemented or tested. Implement using supported demo environment, then verify actual account/tag scope; no offline timer pass is claimed. |
| P8.6 | D / R | Actual exchange-timer trigger plus account/position reconciliation requires timer implementation and demo proof; ACK/stopping heartbeat is not terminal proof. |
| P8.7 | O.7 / D / R | Spot units/quality, latency/reservation/inventory metrics pass. SWAP funding/margin metrics remain O.7; deploy bounded retention/flush and qualify actual observations in D/R. |
| P8.8 | D / R | Scoped whitelist/version/input/gate logs reconstruct synthetic decisions and block on recorder failure. Deployment retention/scheduling and live observations remain. |
| P8.9 | O.7 / D / R | All declared spot fault scenarios pass. Spot/SWAP divergence/recovery requires O.7; actual latency/queue/source assumptions remain D/R. |
| P8.11 | D / R | Synthetic event/block/request/proof deltas and explicit breaches pass. Cancellation is driver-scheduled; freeze numeric budgets and measure actual deployed/exchange timings. |
| P8.12 | O.7 / R | Spot NAV/inventory/net-edge/fees/reservations/loss/subsidy and quality reconcile. Hedge/funding attribution remains O.7; actual independent executable values remain R. |
| P8.13 | O.7 / R | Spot slow adverse/net-flat/transient-depth/latency/fee/turnover ordering passes. Funding/basis shocks require O.7; empirical calibration remains R. |
| P9.1 | D | Build the installation/demo/shadow/start/pause/reconciliation/rollback operator runbook alongside concrete demo endpoint integration; O.6 reproduction is not a complete operational runbook. |
| P9.2 | D / R | Offline no-send guard and insufficient-history evaluation pass. Implement/run real-data shadow with qualified independent LIFE observations when available. |
| P9.3 | D | Implement/verify isolated demo REST/WS routes and credentials; no demo support is inferred from fake connector success. |
| P9.4 | D | Retain frozen fake LIFE proof and pair it with an actually available supported demo instrument; document representativeness limits. |
| P9.5 | D | Run and record actual demo session/expiry/restart/disconnect/cancel soak; no real demo orders were sent during O. |
| P9.6 | D | O.6 local executable regression/diff gate closes only the local component; remote CI and demo release integration remain D, not claimed as passed. |
| P9.7 | R | Build/freeze qualified account/reference/fee/budget production configuration and installation under read-only validation. Live configs currently fail closed. |
| P9.9 | D / R | Synthetic stop/cancel/reconcile/restart primitives pass; integrate and exercise operational rollback with actual demo/account state and residual positions. |
| P9.10 | R / C | Synthetic separation/insufficient-evidence contract passes. Gather qualified disjoint data, uncertainty and cost/queue sensitivity before economic release and canary evaluation. |
| P9.11 | R / C | Software objective and hard budget gates pass. Freeze numeric edge/KPI/drawdown/utilization/stress criteria with rationale; empirical objective feasibility is unproven. |
| P9.12 | R | Synthetic no-model reference comparison and independent attribution pass. Real same-capital no-trade/no-benchmark baselines and disjoint LIFE evaluation are still required; sparse history leaves eligibility unmet. |
| P9.8 | C | Operator alone reviews concrete R evidence and separately decides live permission; no production orders are authorized. |
| P9.13 | C | Operator/risk owner must supply concrete canary capital/budget/duration/sample/stop/scale criteria per product. No automatic capital scaling or extension activation. |

## Explicit limits and next work

- **O.7 remains necessary for the user's full offline scope:** shared spot/SWAP risk allocation, protected SWAP lifecycle, hedging/funding/margin recovery and actual inventory child-order execution. Current advisory decisions and connector slices are not full extension acceptance.
- **A13/P8.5/P8.6 are D/R implementation and proof:** no exchange cancellation timer or stalled-loop heartbeat authority is currently claimed. Local reserved cancellation quota and independent watchdog tests do not prove exchange failover.
- **Basket mode is disabled.** Single-source fallback is covered; a future basket needs its own loss/reweight policy and acceptance before activation.
- **Adaptive quote signals are supplied offline; refresh decisions are advisory.** Qualified production signal acquisition, configuration-driven adapter installation and automatic refresh scheduling remain D/R implementation work.
- **Real account and economic qualification remain R/C:** history pagination/completeness/retention, deployed ownership, real fee tiers/currencies, independent LIFE price/exit depth, physical storage assumptions, API headroom and measured latency. Fake data and the frozen driver-scheduled latency model cannot establish these facts.
- **Economic result remains insufficient evidence.** The O.5 seeds, external-print queue, costs and independent values are synthetic. No claim of profitable market making, affordable live subsidy, guaranteed fills/exit, empirical uptime KPI or capital-scaling eligibility follows from this gate.

Next offline milestone: **O.7**, scoped explicitly to the enabled perpetual,
hedging and inventory-execution features. A separate spot demo milestone may
begin after this spot O closure, with isolated demo endpoints/credentials and
explicit demo limits; this report does not implement or authorize that run.
