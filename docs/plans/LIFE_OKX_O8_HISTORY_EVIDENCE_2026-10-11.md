# O.8 settled-history remediation checkpoint — 2026-10-11

## Verdict

**O.8.4 and O.8.7 remain open.** The complete 10,000-intent/fill history exposes latency failures that the earlier rejection-history sender fixture did not cover. No completion verdict, live permission, economic viability or deployed exchange behavior is established here.

The acceptance manifest now requires `test_o8_settled_history.py` and `test_o8_verified_state.py`. A failed scale gate prevents acceptance; no xfail, skip, raised ceiling or forced rejection substitutes for the final-send test. Existing O.7 mappings and requirement fingerprints are retained.

## Implemented

- Build synthetic settled SPOT histories with matching WAL, reservations, trades, zero actual fee events, independent fill attribution, execution-loss records, capital settlement tombstones, reconciled quote actions and stored executor ownership. Paired BUY/SELL fills at the independent value preserve 10 LIFE, 10 USDT and NAV 20. This is a `profit_mm` scale fixture; it does not calibrate the selected live `liquidity_service` objective or prove its large-cycle performance.
- Requalify immutable terminal history without repeating already completed cross-journal writes. A prior `APPLYING` cut still replays required terminal checkpoints. Missing attribution or loss evidence cannot obtain the completed-history shortcut.
- Construct the runner's union of active/stored wire IDs once per scope check. Batch loss/session verification rather than rereading complete journals for each fill.
- Add exact-byte verified WAL images, immutable reservation evidence, immutable policy views and cached bundle/settlement validation. No cache trusts timestamp/size alone. Checkpoint writers retain atomic replace, file/directory fsync and ambiguous-write fencing where previously required. A second clock check protects the transition from a read view to a write lock.
- Serialize atomic checkpoint payloads into one compact JSON buffer. No financial history is deleted.
- Capture financial diagnostics at explicit `SNAPSHOT` events. Permission/queue/final-send events retain gate outcomes, identities, quote inputs, rejection counts and applicable latency metrics; unsampled financial metrics remain explicitly missing. Recorder failure still denies additional risk. The [current O.5 replay](evidence/life_o5_spot_replay_v2.json) replaces the current comparison target; the [original bundle](evidence/life_o5_spot_replay.json) remains unchanged historical evidence.
- Launch both products' bounded cancel transports before full joint qualification can occupy the event loop. A delayed spot transport regression verifies this ordering. Cancel requests still cannot release claims.

## Measurement contract

Environment: macOS arm64, local APFS, conda `hummingbot`, Python 3.13. All exchange/account inputs are synthetic; no credentials or external exchange calls are used. Dataset construction occurs outside the timed workload.

At 0/1,000/10,000 completed intents with one fill each, the test runs the actual protected propose/filter/executor/final-wire path. The production safety coroutine and an independent heartbeat run alongside qualification, sending, repeated permission checks and watchdog-triggered cancellation. The heartbeat is armed before the first production watchdog iteration. A five-second diagnostic timeout does not widen the cancel-start acceptance ceiling.

The [frozen budgets](evidence/life_o8_offline_budgets.json) are unchanged: qualification 2,000 ms, final send 1,000 ms, event-loop lag 500 ms, cancel start 250 ms, reservation restore 1,000 ms. Coverage instrumentation has the previously declared 5× headroom. Qualification/final-send/cancel timings each have one sample per dataset; heartbeat and repeated permission samples carry median/p95/max. These are offline regression measurements, not production estimates.

The [machine-readable results](evidence/life_o8_history_results.json) retain commands, source hashes, the full regression outcomes and isolated timing samples. The original terminal-checkpoint Red reproduction is retained separately from the still-failing complete-history performance gate.

## Remaining implementation

The new caches bound image count; the WAL cache also caps retained raw bytes at 32 MiB and eight files. This is **not** a bound on campaign journals or total decoded process memory. The current active checkpoints still contain all historical rows and are rewritten for new intent transitions.

O.8.4 Green therefore still requires a versioned settled-history checkpoint/retention implementation, explicitly configured capacities, atomic manifest/segment publication, cold migration/restore/deduplication proofs and bounded cooperative validation. No file lock may remain held across an `await`; a completed worker result must be fenced by current journal versions, session/config/risk epoch, account/runner cut, freshness and clocks before use. Capacity exhaustion must deny new risk while retaining cancellation, recovery and all financial/ownership evidence.

Repeat both `profit_mm` and `liquidity_service` histories where budget-cycle state differs. The existing fee, subsidy, inventory-cycle, SWAP chronology and 100/500-fills-per-order fault suites remain required, but they cannot replace the growing complete-history latency gate.

Close O.8.4 only after every unchanged timing gate passes, then run the full O.8 completion auditor with both pinned changed-line coverage bases before closing O.8.7.

## Reproduction

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_settled_history.py -s --tb=short

conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.o8_acceptance \
  --candidate --output-dir /tmp/life-o8-history-final-audit
```

Both commands must continue to report failure while the mandatory 10k history exceeds its gates. A successful candidate regression alone would still not declare completion while O.8.4/O.8.7 are open.

## Recorded results

The final full-scope candidate audit records **2,062 passed, 1 failed, 0 skipped**. The sole failure is the mandatory 10k settled-history timing gate. All setup/teardown phases pass. The auditor exits before issuing a completion summary.

Isolated, uninstrumented timings (milliseconds):

| Completed intents/fills | Joint poll | Propose → final send | Maximum loop lag | Actual cancel start |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 4.5 | 48.9 | 55.0 | 17.2 |
| 1,000 | 102.0 | 291.1 | 309.7 | 76.8 |
| 10,000 | 1398.9 | 3668.9 | 4322.9 | 734.2 |

The 0/1k cases pass every frozen timing gate. The 10k case remains Red. Cancel start is timestamped at the connector callback, independently of a delayed observer wake-up.

Pre-commit checks pass. Changed-line coverage is **90%** against the pinned feature base and **92%** against the pinned recent base (required: 80%). Coverage results are diagnostic while the scale gate fails; no completion verdict follows from coverage alone. See the JSON results for both pinned bases and exact source hashes.
