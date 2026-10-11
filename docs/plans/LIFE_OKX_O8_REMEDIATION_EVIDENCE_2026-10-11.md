# LIFE / OKX O.8 remediation evidence — 2026-10-11

## Verdict

**O.8 is partially complete. O.8.1, O.8.2, O.8.3, O.8.5 and O.8.6 are verified. O.8.4 and O.8.7 remain open. Demo remains blocked and production order permission remains false.**

This checkpoint implements the six review corrections and adds executable review acceptance. The continuous full-assembly expiry/restart replay passes. Complete growing-history performance remains unverified, so the completion verdict stays open.

Reviewed baseline: `3a2e33aad`. Implementation base: `912bd276df0dc9b3b0b4fae97bd579a772340927`, plus the uncommitted source/test snapshot recorded in the evidence bundle. All accounts, instruments, balances, fills, funding and transport faults below are synthetic; no credentials or exchange requests were used.

## Changes and proof

| Item | Correction | Executable evidence |
| --- | --- | --- |
| O.8.1 | ACTIVE provenance is distinct from quiescent release/rearm. A still-fresh committed receipt survives collection I/O; account sequence, hints, invalid ownership/scope, risk/config context, stale data and collection failures revoke it. Unarmed spot children need an exact captured proposal and durable action identity. | `test_o8_review.py`: ACTIVE polls, in-flight refresh, receipt revocation and unarmed ownership; retained runner/recorder/provenance suites |
| O.8.2 | Hedge-related unavailability drains spot quotes without pausing the parent solely for a required hedge. Qualified coordinator OPEN/CLOSE permission still passes the actual runner and protected final boundary; hard session, risk, carry and wire gates remain binding. | Protected SELL partial-fill → BUY OPEN; continuous BUY → SELL CLOSE and SELL → BUY OPEN; retained final-send revocation matrix |
| O.8.3 | Balanced, affordable pending spot makers are accepted only with matching capital claim and sent/ACKED WAL wire ownership. Unknown claims, wrong wires, missing WAL and unaffordable stress still block. | Both maker sides, one/three levels; pending negative matrix; continuous runner with owned live makers |
| O.8.4 — partial | Aggregate ledger fills once, index prior trades and product WAL records, encode each order once, reuse independently verified exact-byte decoded images, and write compact action JSON in one buffered operation. Required fsync, writer fencing and financial/dedup history remain. | 0/1k/10k restore/action-history scale with production watchdog; 100/500 fills through actual joint qualification/settlement, reversed pages and cold restore; cache tamper/stale-writer tests |
| O.8.5 | Bound cancel transport using the injected freshness deadline; run spot and SWAP cancellation concurrently. A separate cancellation mutex prevents collection I/O from blocking SWAP cancellation. Re-read the current WAL between cancel attempts because collection may settle an order during transport I/O. | Blocked/failing product matrix in both directions, cold sender scope, in-flight collector fault, retained retry/budget/lost-ACK/recovery suites |
| O.8.6 | Persist a collector-qualified causal sequence for SWAP timestamp ties; opaque trade IDs establish immutable identity, not chronology. Canonical checkpoint encoding tolerates reversed page arrival. Missing/conflicting/invalid ordering evidence fails closed. | CLOSE→OPEN and OPEN→CLOSE, `9`/`09`/opaque IDs, reversed input, ambiguous histories retaining claims, correct NAV/release and cold restart; 100/500 partial fills and immutable history |
| O.8.7 — partial | New review manifest preserves O.7 selectors and maps every new review test into the A/P requirements. Completion checks require all seven review rows and the O.8 plan parent to be closed. `--candidate` never sets `offline_complete: true`. | Review-gate negative tests and new continuous trace; complete fake runner/connector/executor/recorder regression and both pinned coverage bases |

The new continuous replay uses actual `StrategyV2Base.tick`, action filtering/dispatch callbacks, `OrderExecutor`, protected final wire checks and the production watchdog coroutine. The exchange collector and orchestrator adapter are fake. Only the production release switch is substituted; the YAML watcher is omitted. Normal progression does not assign `_runner_halt_ok`, manually halt executors, force safety states or directly call joint reconciliation between phases. Inventory enter/return are explicit operator requests and invoke their normal production transitions.

The collector publishes one consistently cut account/carry/history bundle after I/O. Separately published changed account sequences are intentionally unqualified until reconciliation; a separate negative test checks revocation during collection. Real authenticated collection/assembly remains D/R work.

After each trace expires, a new controller, session manager, WALs, reservation ledger, shared capital authority, protected senders, action planner, runtime safety gate, loss/exit budgets, fill attributor, hedge coordinator, carry monitor, inventory ledger/executor and telemetry recorder reopen the durable files. A new actual V2 runner and independent watchdog run three more poll intervals. Financial records match, no order is sent, and expiry, joint/inventory restore latches and safety rearm requirements remain binding. External feeds/transport are retained as fake exchange I/O. This tests expired recovery; it does not claim automatic rearm or production-account installation.

Trace coverage:

- BUY: balanced idle and periodic polling → owned pending maker → terminal partial fill → qualified CLOSE hedge → reconciled MM → operator inventory execution/return → qualified OPEN rebalance → carry failure → expiry.
- SELL: balanced idle and periodic polling → owned pending maker → terminal partial fill → qualified OPEN hedge → reconciled MM → carry failure → expiry.

Inventory is not repeated in the SELL trace: that preceding spot sale already reaches the inventory campaign's configured physical balance floor. The floor is preserved.

## Red → Green

The committed review baseline was checked out separately. The durable review selectors produced **21 failures, 18 deselected**: ACTIVE provenance, refresh liveness, pending-maker variants, OPEN-hedge progression, blocked cross-product cancellation, missing causal-sequence support and 10k restore. Baseline restore measured approximately **3,993 ms**, exceeding the 1,000 ms synthetic ceiling. The chronology variants fail on the baseline's missing sequence API; the earlier review separately reproduced its lexicographic replay failure with `JOINT_CLOSE_PNL_INVALID`.

Two additional regression failures were caught while implementing the fix: an unowned unarmed spot executor was accepted, and a blocked collector mutex prevented SWAP cancellation. Both now have durable passing negative tests.

The [evidence bundle](evidence/life_o8_remediation_results.json) records final source hashes, commands, Red failures, continuous traces and scale measurements. The [regression archive](evidence/life_o8_regression.json) records every final collected test outcome. The audit summary is embedded in the bundle. Final regression: **2,045 passed, zero failed/skipped**, 27 dependency/deprecation warnings, 135.96 seconds. Changed-line coverage is **90.08%** against the pinned feature base and **91.86%** against the pinned recent base (80% required). The separate uninstrumented review run passed **67 tests**, six warnings, 15.39 seconds. The report remains a **candidate**, not a completion certificate.

Before commit, the repository `isort` hook normalized imports in two test files. The review suites passed again: **67 tests**, six warnings, 15.34 seconds. The bundle preserves both the full-audit source snapshot and the post-hook snapshot; the 2,045-test archive and serial performance measurements refer to the full-audit snapshot.

## Offline performance policy

The [frozen numeric budgets](evidence/life_o8_offline_budgets.json) were set before scale validation on macOS arm64 / local APFS / conda Python 3.13. Restore: 1,000 ms; joint qualification/settlement: 2,000 ms; authorization through final send: 1,000 ms; loop-lag upper bound: 500 ms; healthy-product cancel start: 250 ms. Coverage instrumentation has explicit 5× headroom. None configures live trading.

Scale output reports samples, median, nearest-rank p95 and maximum. Each history size currently has five restore samples, one complete propose/filter/final-send sample and four repeated permission samples; the one-send sample does not estimate a production latency distribution. The lag measurement is a conservative bound including synchronous restore/authorization and independent watchdog work. The bundle distinguishes these measurements.

Final serial, uninstrumented measurements in milliseconds:

| History entries | Ledger restore median / p95 / max | Propose → final send (one sample) | Loop-lag bound median / p95 / max |
| --- | --- | --- | --- |
| 0 | 0.08 / 0.17 / 0.17 | 79.39 | 10.04 / 80.16 / 80.16 |
| 1,000 | 8.13 / 8.95 / 8.95 | 93.62 | 17.87 / 101.99 / 101.99 |
| 10,000 | 86.69 / 208.80 / 208.80 | 159.98 | 199.70 / 240.36 / 240.36 |

These measure separate completed-fill reservation restore and completed pre-dispatch action history. They are not measurements of a combined 10k settled-fill account/joint sender history. Repeated permission checks peaked below 9.5 ms in these fixtures. At five samples, nearest-rank p95 equals the maximum; it is not a tail-latency estimate for production.

**Remaining O.8.4:** sender scaling currently uses valid completed pre-dispatch action rejections. Filled reservations are scaled separately, and joint settlement is tested with 100/500 fills in one order. Add complete 0/1k/10k *settled joint/account fill histories* across all journals, exercise the entire authorization and cancellation path with those histories, and justify a bounded checkpoint/retention policy. No financial or deduplication evidence is currently archived away. These are actionable offline tasks.

**Remaining O.8.7:** close O.8.4 and run the completion gate. The sustained full-assembly expiry/restart proof now passes for both maker sides; O.8.7 remains open because it requires all six fixes to meet their own acceptance criteria.

Existing journals containing pre-change encoded trade records are not automatically migrated. Missing causal evidence or an incompatible immutable history must remain blocked; qualification/migration and the actual OKX ordering source must be established before demo/live assembly.

## Reproduce

From the repository root:

```sh
conda run -n hummingbot pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_review.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_continuous.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_performance.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_acceptance.py

conda run -n hummingbot python -m \
  test.hummingbot.strategy_v2.life_liquidity.o8_acceptance \
  --candidate --output-dir /tmp/life-o8-audit
```

Omit `--candidate` only after the remaining offline tasks and plan closure are complete; the auditor currently rejects that verdict. It preserves A01–A39, all previous mapped tests and 118 detailed D/R/C classifications. Both original pinned changed-line coverage bases retain the 80% aggregate gate.
