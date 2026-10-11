# O.8 lazy WAL/reservation checkpoint — 2026-10-11

## Verdict

**Lazy terminal reads and pending/cancel checkpoints are implemented. O.8.4/O.8.7 remain open.** This increment bounds the retained decoded terminal cache of each WAL/reservation branch by one explicitly sized segment. It does not establish bounded whole-campaign RAM, history-independent integrity/qualification, passing latency gates, future-settlement headroom, demo eligibility or production permission.

This report supersedes the full decoded WAL/reservation restoration described in the earlier [journal checkpoint](LIFE_OKX_O8_JOURNAL_EVIDENCE_2026-10-11.md). Earlier archive/worker/journal reports remain historical source snapshots. Final commands, outcomes, exact source hashes and full latency samples are recorded in [lazy-journal results](evidence/life_o8_lazy_journal_results.json); per-test phases are in [regression evidence](evidence/life_o8_lazy_journal_regression.json).

## Implemented boundary

- Existing explicit schema-2 journal policies now restore sorted terminal maps lazily. Each branch retains segment descriptors/key ranges and at most one immutable decoded chunk, bounded by `records_per_segment` and `max_segment_bytes`. Segment metadata and active state remain bounded by the caller's manifest/segment capacities. Pending typed rows stay in RAM. Historical audit APIs can deliberately materialize full results; this is not a whole-process constant-memory claim.
- Terminal intent/reservation/trade/fee lookups select a segment by sorted key range. WAL wire lookups use a derived 16,384-bit (2 KiB) membership filter per segment and then compare exact rows. Filters can only select candidates; false positives cannot establish identity and repeated filter-bit positions cannot produce false negatives. A wire dictionary is retained only for the single cached segment, bounded by its row count; no dictionary covering all terminal wires is retained.
- Cold restore verifies all segment digests, coverage, sorted nonoverlapping map ranges, ownership and terminal classification. Private disposable SQLite indexes, with a 256 KiB page-cache target, validate duplicate wires and exact Decimal fill sums without retaining a Python index per historical row. Scratch indexes are discarded before eligibility; they are not persisted financial authority. Trade iteration preserves canonical key order across active/settled partitions. Missing or invalid evidence and unavailable scratch storage fail closed. Restore rechecks the root after financial/identity validation.
- A cached immutable point lookup represents an authenticated snapshot and grants no order permission. Permission/commit verification independently rehashes **all current referenced segment bytes**, even when the point cache is warm. No timestamp trust is introduced. This distinction avoids rereading/hashing an entire segment for every historical row. A dedicated regression proves repeated point-cache reuse, then proves warm permission denial after segment corruption.
- WAL pending reconciliation/order-scope queries use active rows. Reservation exposure and planning previews use active reservations while retaining complete historical filled turnover and terminal-ID duplicate checks. Proposal identity checks use point membership instead of constructing every WAL ID. Pre-send provenance and attribution source comparisons use verified immutable cuts rather than full WAL/reservation snapshots solely for equality.
- WAL prepared/armed/acknowledged/cancel updates and reservation cancel/unknown updates can publish the active root while retaining every terminal descriptor. Pending publication uses the owner's writer lock, exact-root CAS, atomic replacement and file/directory fsync. Ambiguous replacement locks the current writer; cold restore sees the complete old or new cut. No active row or terminal evidence is deleted on this path. Reservation cancel bookkeeping changes no balance, fill quantity or fee event.
- Attribution readiness caches retain immutable lazy WAL/reservation views, rather than copying their complete decoded histories. Warm readiness rechecks archived bytes before cache reuse. Legacy mutable inputs still receive detached snapshots. Archive migration evicts retained legacy WAL images and obsolete reservation verification maps.
- No schema/policy auto-detection, controller/YAML archive activation, archive pruning, live default, economic threshold or production order switch changes. The new suite is mandatory for O.8.4 and A24/A35/A36.

## TDD and verification

The initial lazy restore/active-query/update regressions recorded **6 failures** before implementation. Pending-checkpoint regressions recorded **2 failures** before active-only publication. Attribution-cache coverage recorded **1 failure** before retaining lazy views and checking warm segment integrity. Repeated snapshot lookup recorded **2 failures** before decoded-chunk reuse; the initial lazy implementation was measurably slower because it reread segments per row. A further scale reproduction measured 1,178 segment reads for 1,000 wire queries with a saturated 256-byte filter; the fixed per-segment filter was increased to 2 KiB without making it identity authority. A rehashed numeric trade owner reproduced one further failure before strict owner-string validation prevented SQLite type coercion, preserving the legacy identity contract. These intermediate scale/candidate probes are not final-source acceptance evidence.

The new suite contains **36 cases**, including 0/1k/10k retained-cache bounds, complete historical turnover, terminal intent/wire/fill/fee deduplication, rehashed invalid financial/identity evidence, overlapping segment ranges, same-size corruption, stale cold roots, read-only views, filter collisions, pending publication faults and actual assembled attribution readiness. Retained-cache assertions describe the journal maps and their caches, not total process RSS or transient financial-write allocations.

The final focused command passed **339 tests, 0 failed, 0 skipped**, with six existing warnings, in **22.65 seconds**. It includes all **36** new cases and retained archive, worker, joint recovery, verified-state, reservation persistence, acceptance, risk, WAL, attribution, controller, quote-action and gateway regressions. The exact command is in the machine-readable results. Python lint/import/format hooks passed. No latency ceiling changed.

## Isolated scale measurements

The final isolated full matrix passed **8 cases and failed 8**, with six warnings, in **115.53 seconds**. Every frozen format/history variant is retained. Both 10k worker variants time out before final send; the thread 1k case also fails. Legacy 1k/10k, segmented 10k and journal 1k/10k exceed unchanged timing limits. These observations do not establish an overall speed improvement; the read-count regression proves the specific cache/filter mechanism, while complete financial/provenance processing remains history dependent.

| Format/backend | Completed history | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy | 0 | 5.1 | 54.6 | 61.1 | 16.4 |
| Legacy | 1,000 | 412.2 | 1122.8 | 1144.4 | 97.8 |
| Legacy | 10,000 | 4775.0 | 5599.8 | 5846.6 | 1646.3 |
| Segmented | 0 | 15.4 | 126.8 | 129.7 | 19.3 |
| Segmented | 1,000 | 107.6 | 409.8 | 426.7 | 59.8 |
| Segmented | 10,000 | 2755.6 | 3598.0 | 3755.6 | 341.3 |
| Segmented/thread | 0 | 7.7 | 73.0 | 79.2 | 9.8 |
| Segmented/process | 0 | 19.4 | 75.1 | 73.2 | 10.1 |
| Segmented/process | 1,000 | 239.0 | 326.7 | 340.5 | 51.5 |
| Segmented/journals | 0 | 18.5 | 200.0 | 206.6 | 36.8 |
| Segmented/journals | 1,000 | 529.5 | 2540.5 | 2664.0 | 159.3 |
| Segmented/journals | 10,000 | 2589.2 | 12991.1 | 13807.2 | 963.9 |
| Segmented/thread | 1,000 | timeout | not reached | incomplete | not reached |
| Segmented/thread | 10,000 | timeout | not reached | incomplete | not reached |
| Segmented/process | 10,000 | timeout | not reached | incomplete | not reached |

The complete-history fixture retains its explicit synthetic capacities: 500 rows per segment, 1 MiB segments, 64 MiB archive allocation, 512 segments and 256 KiB active manifest. The retained-cache unit proof uses the same capacities at 0/1k/10k: 64 rows, 64 KiB segments, 16 MiB archive allocation, 512 segments and 256 KiB active manifest. The wire-filter test uses 5,000 rows, ten 500-row segments and 1,000 queries with fewer than 50 segment reads. These are technical offline fixtures, never live defaults. No opening-capital, fee/loss/subsidy or future-settlement budget is inferred from them.

## Full candidate

The complete final-source candidate collected **2,213 tests: 2,206 passed, 7 failed, 0 skipped**, with 27 existing warnings, in **351.15 seconds**. Every setup/teardown phase passed. All 36 new lazy-journal cases passed. Six failures are complete-history scale cases: legacy/segmented 10k, both 10k worker variants and journal 1k/10k. The unchanged frozen 5× instrumentation headroom applies only to the declared scale budgets; worker refresh deadlines remain unchanged.

The seventh failure is `test_carry_at_actual_swap_rest_throttler[True]`: a 2-second asynchronous gather deadline expired in the full coverage run. Both `True` and `False` selectors passed a standalone recheck (**2 passed**, 8.06 seconds). This does not establish the cause or resolve the full-run failure; it is retained as an additional O.8.7 blocker. Its deadline was not widened.

Coverage was collected in the full candidate, but regression failures prevented both pinned changed-line comparisons and the completion summary. No earlier coverage percentage is reused. Source hashes cover the exact 26 changed/new Python files tested in this snapshot. The candidate outcome and every per-test phase remain in the linked machine evidence; old/interrupted probes do not substitute for this completed run.


## Remaining O.8.4 work

1. Bound the complete campaign working set across capital/joint state, attribution events, loss/subsidy/exit budgets and action/executor ownership, including transient allocations. New reservation, fill/fee/cash-flow and terminal-settlement writes still materialize historical maps and may revalidate them.
2. Make complete integrity/provenance/qualification and final-send work cooperative and version fenced, without holding file locks over awaits. Exact-byte verification remains synchronous and proportional to history; this increment does not claim bounded event-loop delay.
3. Reserve storage for every outstanding intent's future settlement, cancellation and recovery before admitting new risk. Existing capacity refusal preserves claims but does not guarantee future settlement capacity.
4. Qualify the full-run carry throttler timeout under the complete instrumented assembly; a standalone pass cannot erase it.
5. Pass every unchanged legacy/segmented/thread/process/journal 0/1k/10k gate and then the O.8.7 completion gate, including both pinned changed-line coverage comparisons. No raised ceiling, rejection-only fixture or earlier evidence substitutes for an affordable final send.
