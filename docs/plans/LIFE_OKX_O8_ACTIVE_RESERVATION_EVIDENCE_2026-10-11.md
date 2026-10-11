# O.8 archived new reservation admission — 2026-10-11

## Scope and verdict

**Archived zero-fill admission no longer copies settled financial rows or reloads the reservation ledger. O.8.4/O.8.7 remain open.** This checkpoint covers `reserve()` and isolated SELL `reserve_risk_reduction()` only. Legacy admission and financial settlement retain their existing complete-history write path. No trading permission, timing ceiling, worker deadline, archive bound or live default changed.

[Machine evidence](evidence/life_o8_active_reservation_results.json) retains commands, individual case outcomes, all six timing/profile reports, scale samples and source/budget/manifest hashes. The baseline is the previous uncommitted [spot journal proof checkpoint](LIFE_OKX_O8_SPOT_PROOF_EVIDENCE_2026-10-11.md), pinned by its source and artifact digests. Git HEAD alone does not reproduce that baseline. Only `risk.py` changes among the profiled production files relative to it; earlier evidence remains unchanged.

## Implementation and safety contract

- Public admission retains its duplicate, inventory, balance, gross/net exposure and isolated exit checks. Under the memory and writer locks, archived admission independently verifies the complete disk/memory cut and capacity, then publishes one additional active reservation.
- The active root preserves existing trades, account events, balances and terminal archive descriptors. Typed immutable financial maps and the derived total filled quantity remain unchanged. Admission does not enumerate settled reservations, decode historical financial rows or rebuild a cold ledger.
- Memory verification now includes the derived filled total in both comparison and cache identity. A divergent balance, trade, policy or filled total cannot be promoted into a new sealed evidence image. Stale writers, uncertain journals, corrupt/missing/symlink segments and duplicate identities still deny admission before publication.
- Publication retains exact-byte verification, root CAS, file and directory fsync, atomic replacement and uncertain-writer denial. Cold restore after interrupted publication sees the old state or the complete new hold; a killed process cannot cause a second admission of the same identity.
- Cancel/unknown state checkpoints share the active publication helper. Over-capacity new risk is refused while existing claims and the state-only cancel path remain available. This does not pre-reserve future fill/settlement/cancel/recovery storage or make historical integrity reads asynchronous.

## TDD and regression

Recorded Red: **17 failed / 27 passed / 0 skipped**, six existing Pydantic warnings, **19.71 seconds**, against unchanged baseline production code. Nine failures establish settled enumeration/reload at 0/1k/10k across BUY, SELL and isolated exit admission. Eight establish missing pre-publication memory integrity for balance, trade, policy and derived filled total in BUY/exit admission. In the trade-divergence case, the old path detected the conflict after changing the durable root; the regression requires rejection before publication.

An earlier probe incorrectly called `reopen()` on an uncertain instance. Those fixture errors were corrected to explicit cold restore before the recorded Red. Two partial-active preservation cases were added afterward, bringing the final new suite to **46 cases**.

The suite covers:

- Guarded settled iteration and cold financial reload at every frozen history size, unchanged segment bytes/descriptors, historical fill/fee deduplication and restored balances/holds.
- Independent memory/disk integrity, same-size/same-stat corruption, changed limits, stale writers, missing/symlink segments and uncertain state.
- File fsync, replacement before/after, directory fsync and post-publication verification faults; a real child process killed immediately after root replacement; explicit cold restore and retry denial.
- Duplicate and risk-limit denials, competing holds, active-byte/orphan-allocation refusal with retained cancel/recovery, and existing partial fills/fees/cancel state across new BUY/SELL holds.

Final focused verification: **489 passed / 0 failed / 0 skipped**, six existing warnings, **155.66 seconds**. It includes reservation persistence, archive/lazy integrity, quote/executor/final-send, hedge, joint recovery, continuous runner, inventory/exit, spot-session replay and executable acceptance tests. The new suite is mandatory in O.8.4 and mapped A24/A35/A36; a regression prevents dropping it from the gate.

## Measurement method

Six sequential fresh processes run timing and cProfile/I/O/GC diagnostics at 0/1k/10k using the original settled-history workload and frozen budgets. Setup/migration is excluded from hot-path profiles. Each configuration has one sample; host variation and profiler overhead prevent causal elapsed-time speedup claims. Exact-byte reads count returned bytes, not physical disk traffic; process peak RSS includes fixture construction and is not a bounded active-memory proof. Cumulative function times overlap and must not be summed.

The unchanged 16-case scale matrix is separate from diagnostics. Failed formats/backends remain recorded, with no substitutions or widened deadlines. Full candidate regression, the retained carry timeout, both pinned coverage comparisons and completion are not requalified by this focused checkpoint.

### Timing diagnostic

| Settled intents | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 10.3 | 91.4 | 98.0 | 14.9 | Pass |
| 1,000 | 576.3 | 819.2 | 840.0 | 43.1 | Fail: loop lag |
| 10,000 | 4,220.5 | 5,328.4 | 5,604.7 | 363.8 | Fail: qualification/send/loop lag/cancel |
| Frozen ceiling | 2,000 | 1,000 | 500 | 250 | Unchanged |

### Profiled filter/dispatch/final-send

| Settled intents | Prior `reserve()` cumulative ms | Current `reserve()` cumulative ms | Prior → current financial archive reload calls |
| --- | ---: | ---: | ---: |
| 0 | 3.29 | 2.34 | 2 → 0 |
| 1,000 | 192.13 | 14.77 | 2 → 0 |
| 10,000 | 2,408.95 | 48.24 | 2 → 0 |

At every profiled size, the sender still records zero WAL `all_records()` calls. The guard tests establish that admission does not enumerate settled financial maps or call the cold loader; the profile independently confirms the disappearance of two financial reloads. Exact historical-byte reads remain proportional to retained history, so this is not constant-time admission.

At 10k, this stage records **5,787.6 ms** wall and **5,576.7 ms** process CPU, with **8,366** counted reads returning **1,017.3 MiB**. Prior observations were 9,225.7 ms wall, 8,986.0 ms CPU, 8,808 reads and 1,036.3 MiB. These single observations do not isolate host effects. Freeze calls fall from **2,221,507 to 920,448**, and financial archive reloads from **2 to 0**. Exact segment verification calls increase from **122 to 124**: the independent pre-publication verification remains required.

Remaining overlapping cumulative costs include attribution readiness (**3,180.3 ms**), exact segment verification (**873.7 ms**) and freezing (**1,168.7 ms**, including **764.4 ms** self time). The 20,002 WAL decode calls remain elsewhere in the stage. Fsync takes **2.21 ms** across 12 calls. The separate profiled proposal-to-send clock is **8,188.9 ms** and includes proposal work outside this sender stage; it is not an acceptance latency measurement.

## Full scale matrix and remaining work

The unchanged matrix records **8 passed / 8 failed / 0 skipped**, **67.83 seconds**, 21 warnings (six Pydantic plus fifteen pytest/JUnit property warnings), using **1× frozen budgets**. Thirteen cases produce timing samples; both 10k worker cases fail before the final-send sample, and the terminal no-recheckpoint regression has no timing sample.

Failures remain:

- Legacy 10k: qualification, send, loop lag and cancel-start ceilings.
- Segmented 1k: loop lag; segmented 10k: send, loop lag and cancel-start ceilings.
- Segmented/thread 10k and segmented/process 10k: `JOINT_HISTORY_WORKER_TIMEOUT`.
- Segmented/process 1k: loop lag.
- Segmented/journals 1k: loop lag; segmented/journals 10k: qualification, send and loop lag.

The mandatory 10k journal sample records **2,704.6 ms** qualification, **4,582.0 ms** propose → final send, **4,751.7 ms** maximum loop lag and **222.7 ms** cancel start. The separate timing diagnostic records a failed cancel ceiling as well; neither observation replaces the other. Timing varies even in paths unchanged by this increment, so the evidence establishes removed work and retained failures, not a stable end-to-end speedup.

Next work is verified financial/attribution state reuse, then remaining financial writes and bounded watchdog/worker scheduling. Exact historical-byte verification, proposal action/runner scans, complete campaign memory, future settlement/cancel/recovery headroom and the unresolved full-run carry timeout remain. O.8.4/O.8.7 stay open until the unchanged scale, full candidate, both pinned coverage comparisons and completion gates pass. No aggregate offline completion is claimed.
