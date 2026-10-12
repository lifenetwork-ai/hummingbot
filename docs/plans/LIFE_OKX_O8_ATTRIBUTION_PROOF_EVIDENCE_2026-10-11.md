# O.8 financial attribution proof reuse — 2026-10-11

**The narrow proof-reuse mechanism passes 613 focused tests. The unchanged full scale matrix remains Red: 10 passed / 6 failed / 0 skipped. No aggregate offline completion is claimed.**

## Scope

This checkpoint optimizes financial readiness across nonfinancial order transitions in archived WAL/reservation journals. It does not change order, session, account, terminal, reconciliation or send permissions. O.8.4/O.8.7 remain open until their unchanged full gates pass.

[Machine evidence](evidence/life_o8_attribution_proof_results.json) retains individual test outcomes, commands, all six timing/profile reports, scale samples and source/budget/manifest hashes. The baseline is commit `80fbd4ffa` and the immutable [active reservation admission evidence](LIFE_OKX_O8_ACTIVE_RESERVATION_EVIDENCE_2026-10-11.md). Only `fill_attribution.py` changes among profiled production sources. Prior evidence remains unchanged.

## Implementation and safety contract

- Every readiness call still independently verifies exact archived WAL/reservation disk bytes and memory, uncertainty and required source paths. Attribution and all loss/service/exit budget bytes remain part of the proof cut. File metadata and caller revision counters cannot authorize reuse.
- Reuse requires unchanged settled WAL/reservation descriptors, balances, limits, trades, fees, account events, attribution/cashflow events, approvals and budget policies/entries. Filled active reservations retain their complete immutable intent, filled quantity and WAL identity/session/epoch/slot provenance.
- Only zero-fill active holds and nonfinancial lifecycle fields are excluded from the financial projection. ACK, cancel, attempt timestamps and unknown state still pass their own journal and order permission checks. New fills/fees/cashflows, changed budgets, provenance or settled cuts cause full financial replay. Legacy journals retain whole-cut invalidation.
- One existing detached readiness proof is retained; state-only hits do not append cache entries or update it from unvalidated live data. Cold restore performs full validation. Exact byte verification, history-sized attribution/budget snapshots and changed-cut/cold replay remain; this is not constant-time validation or bounded whole-campaign RAM.
- Detached policy snapshots now copy nested cashflow timestamps. On a miss, mutable inputs are captured before full validation and independently compared with a final snapshot before publishing proof. This prevents mutable aliases from turning a changed input into supposedly validated evidence.
- No financial write format, archive bound, latency ceiling, worker deadline, live default or production permission changes.

## TDD

The corrected baseline Red records **17 failed / 35 passed / 0 skipped** across 52 cases, six existing Pydantic warnings, **66.96 seconds**. Twelve failures establish unnecessary replay for zero-fill and partial-filled active lifecycle changes; three exercise the actual protected sender at 0/1k/10k; two expose aliased cashflow timestamp policy snapshots. A previously unreached fixture used unslotted `prepare()` after slotted history; it was corrected to slotted `begin()` and `arm_send()` before this complete Red was rerun against unchanged HEAD production code.

Four additional publication-race cases ran against the initial optimized candidate: **4 failed / 0 passed / 0 skipped**, six warnings, **6.51 seconds**. They change a transfer timestamp or independent fill value during financial replay in legacy/archived formats. The candidate patch and fingerprints are retained in machine evidence. The final new suite has **56 cases**.

The suite includes guarded full replay/settled iteration, zero-fill BUY/SELL/isolated exit lifecycles, partial-filled active ACK/cancel/unknown transitions, real protected dispatch/final-send, changed fill/fee/cashflow/budget/attribution and active/settled WAL provenance, independent memory and same-stat disk corruption, missing/symlink segments, stale/uncertain writers, cold restore, legacy invalidation, cache aliasing and proof-publication races. A final network-boundary regression proves an unattributed cashflow revokes sending while capital claims and reservations remain held.

Final focused verification: **613 passed / 0 failed / 0 skipped**, six existing warnings, **298.68 seconds**. Coverage includes financial attribution/cashflows and loss/service/inventory budgets, archive/lazy integrity and reservations, quote/executor/final-send, hedge/joint recovery, continuous runner, inventory/exit, spot-session replay and executable acceptance. The new suite is mandatory in O.8.4 and mapped A24/A35/A36; a regression prevents removing it from the gate. This is a focused run, not the complete candidate or a coverage qualification.

## Measurement method

Six sequential fresh processes run timing and cProfile/I/O/GC diagnostics at 0/1k/10k using the original workload and frozen budgets. Setup/migration is excluded from hot-path profiles. One observation per configuration and host variation prevent causal end-to-end speedup claims. Profile timings include diagnostic overhead; cumulative function times overlap. Returned bytes are not physical disk traffic; process peak RSS includes fixture construction and is not active-memory proof.

The complete scale matrix is measured separately with 1× frozen budgets. Full candidate regression, the retained full-run carry timeout, both pinned coverage comparisons and completion are outside this narrow checkpoint.

### Timing diagnostic

| Settled intents | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 17.4 | 130.3 | 130.9 | 16.8 | Pass |
| 1,000 | 588.8 | 408.8 | 625.8 | 44.1 | Fail: loop lag |
| 10,000 | 3,949.7 | 4,108.3 | 4,508.1 | 559.2 | Fail: qualification/send/loop lag/cancel |
| Frozen ceiling | 2,000 | 1,000 | 500 | 250 | Unchanged |

### Profiled filter/dispatch/final-send

| Settled intents | Prior → current `ready()` cumulative ms (7 calls) | Prior → current full source/capital replays | Prior → current WAL decode calls |
| --- | ---: | ---: | ---: |
| 0 | 12.74 → 13.46 | 2 → 0 | 2 → 2 |
| 1,000 | 330.94 → 34.97 | 2 → 0 | 2,002 → 2 |
| 10,000 | 3,180.25 → 210.19 | 2 → 0 | 20,002 → 2 |

Both `_durable_sources_match()` and `capital()` disappear from this warm sender stage at every profiled size. WAL `all_records()` remains absent. The two remaining WAL decodes are active records; historical provenance still receives complete validation on cold use or a relevant changed cut. Four financial projection calls cover two lifecycle-only cache differences; every one of the seven readiness calls still performs independent disk/memory verification.

At 10k, the sender stage records **2,987.0 ms** wall and **2,791.1 ms** process CPU, versus prior observations of 5,787.6 ms and 5,576.7 ms. Counted reads are **7,776**, returning **969.7 MiB**, versus 8,366 and 1,017.3 MiB. Freeze calls fall from **920,448 to 220,208** and exact segment verification from **124 to 116**; full historical integrity checks remain on every proof use. These single observations establish removed work and retain host variation; they do not establish a stable end-to-end speedup.

Remaining cumulative costs overlap: exact segment verification **913.8 ms**, joint freshness **972.9 ms**, policy-state verification **934.8 ms**, and freezing **320.0 ms**. Fsync is **1.98 ms** across 12 calls. The separate profiled proposal-to-send clock is **5,489.7 ms** and includes proposal work outside this stage. Financial writes, capture/verification of other journals and synchronous integrity reads remain material work.

## Full scale matrix and remaining work

The unchanged 16-case matrix records **10 passed / 6 failed / 0 skipped**, **60.76 seconds**, 21 warnings (six Pydantic plus fifteen pytest/JUnit property warnings), with **1× frozen budgets**. Thirteen cases produce timing samples; both 10k worker cases fail before final-send sampling, and the terminal no-recheckpoint regression has no timing sample.

Failures:

- Legacy 10k: send, loop lag and cancel-start ceilings.
- Segmented 1k: loop lag; segmented 10k: send, loop lag and cancel-start ceilings.
- Segmented/thread 10k and segmented/process 10k: `JOINT_HISTORY_WORKER_TIMEOUT`.
- Segmented/journals 10k: qualification, send and loop lag.

The mandatory 10k journal sample records **2,825.2 ms** qualification, **2,916.1 ms** propose → final send, **3,089.3 ms** maximum loop lag and **233.2 ms** cancel start. Journal 1k and process 1k pass in this run; the separate timing diagnostic still fails journal 1k loop lag and journal 10k cancel start. All observations remain evidence; no failed variant or diagnostic is replaced by a passing sample. Unchanged paths also vary across runs, so the change from 8 to 6 failed cases cannot establish a stable scheduling improvement by itself.

Next work is remaining financial writes and bounded capture/verification/scheduling across the other journals. Synchronous historical-byte checks, proposal action/runner scans, complete campaign RAM, future settlement/cancel/recovery storage headroom and the retained full-run carry timeout remain unqualified. Cold/changed attribution still validates complete history. O.8.4/O.8.7 stay open until unchanged scale, full candidate, both pinned coverage comparisons and completion gates pass.
