# O.8 hedge WAL materialization fix — 2026-10-11

## Verdict

**The two redundant hedge WAL history reads are fixed and remeasured. O.8.4/O.8.7 remain open.** Protected sender profiling now records **5** `all_records()` calls instead of **35**, with **zero hedge coordinator callers**, at every measured history size. Exact-byte WAL verification, account qualification, shared capital checks and claim retention remain mandatory. This increment grants no demo/live permission and does not close the full latency, memory/storage or acceptance gates.

[Machine evidence](evidence/life_o8_hedge_wal_results.json) contains Red/Green outcomes, the full scale matrix, source/budget/manifest hashes and links to six portable timing/profile reports. [The prior diagnostic baseline](LIFE_OKX_O8_PROFILE_EVIDENCE_2026-10-11.md) remains unchanged. Of the production sources covered by that baseline, only `hedge_coordinator.py` changed. No timing ceiling, worker deadline, synthetic capacity or live default changed.

## Changes and preserved checks

- `HedgeCoordinator._snapshot()` calls the existing `IntentWAL.assert_verified()` instead of constructing complete disk and memory WAL tuples for comparison. That API independently checks disk bytes and memory before sharing an immutable verified cut. Legacy and explicit archived journals remain supported; no manifest-only or mtime cache is introduced. WAL failures still deny hedge evidence.
- `allows_spot()` retrieves each observed pending maker once by exact intent ID. It checks the capital claim, SPOT product, wire ID and allowed WAL state (`SEND_UNKNOWN`/`ACKED`) without constructing a dictionary for every historical intent. Missing or terminal/prepared pending records deny permission and retain claims.
- The identity check now also binds the WAL row's session/epoch to the pending capital claim. Valid Red cases showed the previous local hedge check accepted coherent disk/memory rows with a conflicting session or epoch. This is an additional fail-closed identity check; it does not replace controller/final-send session/config/risk fences.
- Quote/executor full-history ownership/terminal consistency checks remain intact. Removing them without an independently verified settled relationship proof would weaken coverage for historical reservations and aborted intents.

## TDD and functional verification

The valid Red command ran the new `test_hedge_wal_scope.py` suite against unchanged production code: **14 failed / 20 passed**, with six warnings, in **15.34 seconds**. Ten failures cover historical materialization and exact pending lookup; four expose session/epoch mismatch acceptance in legacy/archived formats. An initial pending fixture lacked a refreshed joint receipt and was corrected before this Red run; its setup failures are excluded from TDD evidence.

The 34 cases include:

- Healthy 0/1k/10k legacy and archived history, with the historical materialization API guarded on the WAL class, including independently restored images. Historical terminal identities remain available afterward.
- Actual protected maker dispatch, account refresh and joint reconciliation, followed by exact pending lookup for both permitted WAL states. Each maker is retrieved once and its capital claim remains held.
- Warm same-size/same-stat disk corruption, historical WAL memory divergence, financial memory divergence, uncertain WAL, missing pending intent, wrong wire, terminal/prepared state, session/epoch conflict, capital-claim conflict and account mismatch. Coherent identity faults update both disk and memory; denial cannot rely only on a stale cache.

Final focused verification passed **307 tests, 0 failed, 0 skipped**, with six existing Pydantic warnings, in **31.26 seconds**. It includes the new suite, hedge coordinator and actual REST-throttler runner, joint binding/recovery, protected sender session/risk tests, verified-state/corruption cases, continuous maker/hedge/recovery scenarios, lazy journals and acceptance validation. The new suite is mandatory in O.8.3/O.8.4 and their mapped A-cases. Two additional acceptance tests prove it cannot be removed from either review row.

## Remeasurement

The same diagnostic helper ran six times, sequentially in fresh processes: timing and profile modes at 0/1k/10k. Each run pins sources and confirms they stayed unchanged. Setup/migration is excluded from the hot-path profile. Timing mode uses stage clocks only; profile mode adds CPU/I/O/GC instrumentation. The reports preserve failed assertions and the CLI's nonzero outcome.

### Timing mode: current source

| Settled history | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 9.9 | 88.3 | 93.9 | 12.0 | Pass |
| 1,000 | 217.5 | 702.5 | 716.0 | 40.0 | Fail: loop lag |
| 10,000 | 2,465.2 | 6,984.0 | 7,147.7 | 221.1 | Fail: qualification/send/loop lag |
| Frozen ceiling | 2,000 | 1,000 | 500 | 250 | Unchanged |

The prior single diagnostic samples recorded 3,682.6/39,705.3 ms for 1k/10k propose → final send. These are observations from different processes under variable host scheduling, not a controlled elapsed-time speedup estimate. The deterministic caller-count change below is the evidence for the targeted mechanism. At 10k, current filter/dispatch/final-send alone takes 5,878.2 ms wall and 5,869.4 ms process CPU; substantial CPU work remains.

### Profile mode: filter/dispatch/final-send

| Metric | Baseline 1k | Current 1k | Baseline 10k | Current 10k |
| --- | ---: | ---: | ---: | ---: |
| `all_records()` calls | 35 | 5 | 35 | 5 |
| Hedge coordinator callers | 30 | 0 | 30 | 0 |
| `all_records()` cumulative ms | 1,501.5 | 155.9 | 11,902.7 | 1,637.2 |

At 10k, the current profiled sender stage takes **12,976.4 ms**, compared with the earlier profiled sample of 23,611.8 ms. It still returns **1,043.3 MiB** through 8,683 counted file reads, versus 1,161.9 MiB through 9,283 before. Lazy exact-byte verification still runs **112 times**; corruption checks were not skipped to reduce the WAL scan count. These are bytes returned to Python, not physical disk traffic. Fsync remains approximately 2.15 ms in this sample.

Remaining profiled costs overlap and must not be summed: attribution readiness takes 3,224 ms cumulative, reservation `reserve` takes 2,307 ms, and the two archived financial reloads take 1,216 ms. Lazy chunk decode/freeze work remains substantial. The five historical WAL materializations now come only from quote consistency and protected executor submit/authorization. No whole-campaign constant-memory or history-independent verification claim follows from this fix.

## Full unchanged scale matrix

The mandatory 16-case suite passed **8 and failed 8**, with no skips, in **66.29 seconds**, using **1×** frozen ceilings. Twenty-one warnings comprise six existing Pydantic warnings and fifteen pytest `record_property`/JUnit xunit2 warnings.

Failures retained in the machine evidence:

- Legacy 10k: send, loop lag and cancel-start ceilings.
- Segmented 1k: loop lag; segmented 10k: send, loop lag and cancel-start ceilings.
- Segmented/thread 1k: loop lag; segmented/thread 10k and segmented/process 10k: `JOINT_HISTORY_WORKER_TIMEOUT` before final send.
- Segmented/journals 1k: loop lag; segmented/journals 10k: qualification, send and loop lag.

In this matrix the 10k journal sample records **8,456.4 ms** propose → final send, **8,636.5 ms** maximum loop lag and **222.0 ms** cancel start. The separate diagnostic timing sample above is retained as well; neither replaces the mandatory matrix. All formats/history variants and original ownership/affordability assertions remain present. A passing cancel-start sample does not waive earlier event-loop stalls.

## Remaining work and commit boundary

The targeted hedge fix and its regression contract are complete. The next change should address the remaining settled cross-journal coverage/provenance work and financial/attribution cache invalidation, using the retained profile before choosing another optimization. Pending-only lookups cannot replace full terminal/aborted ownership coverage without a verified proof bound to the relevant immutable cuts. Exact historical corruption checks, financial-write/transient allocations, whole-campaign memory and future-settlement/cancel/recovery storage reservations remain required. Both 10k worker timeouts remain; the earlier full-run carry timeout is not requalified by this focused/scale run.

Full candidate regression, both pinned coverage comparisons and the O.8.7 completion gate were not rerun. No O.8 closure commit is warranted by these results. The working tree also contains the preceding archive/lazy/profiling foundations on which this fix and its tests depend; this report does not present an isolated partial commit as a runnable clean checkout.
