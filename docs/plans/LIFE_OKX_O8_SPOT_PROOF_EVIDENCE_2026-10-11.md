# O.8 spot journal ownership proof — 2026-10-11

## Verdict

**The remaining five sender WAL materializations are removed while retaining complete WAL/reservation coverage. O.8.4/O.8.7 remain open.** The actual protected sender records zero `IntentWAL.all_records()` calls at every profiled history size (0/1k/10k). This closes the narrow settled relationship proof checklist item; the full unchanged scale matrix remains **8 passed / 8 failed / 0 skipped**. No demo/live permission, latency ceiling, worker deadline or live default changed.

[Machine evidence](evidence/life_o8_spot_proof_results.json) retains commands, case outcomes, thirteen completed scale samples, all six timing/profile reports, source/budget/manifest hashes and the prior [hedge WAL evidence](LIFE_OKX_O8_HEDGE_WAL_EVIDENCE_2026-10-11.md) digest. Baseline production commit: `f9dae5d9cdd6abe4ded952bbc85ebb988c1ac595`. Production changes are limited to the new `spot_journal_proof.py` and its quote/executor integrations. Older evidence is retained unchanged.

## Implementation and safety contract

- One disposable proof slot is shared by the planner and sender bound to the same WAL and reservation ledger. Every use first runs independent exact-byte disk and financial-memory verification. Neither file metadata nor an archive manifest alone authorizes proof reuse.
- Archived settled branches are merge-validated over their complete canonical key union, with segment-sized decoding. A missing required reservation, an orphan reservation, a noncanonical intent/reservation identity, session/epoch/side conflict or incompatible terminal state denies permission. An aborted-before-send WAL may have no reservation or a matching terminal reservation; an open reservation still denies permission.
- A proof is reused only for the same immutable verified pair: legacy map identity with strong references, or archived transform/segment/directory descriptors. It is not persisted. Changed settled cuts and cold restore establish complete coverage again. A second independent verification after a cold scan rejects a changed cut before publishing the proof.
- Archived active rows are checked on every call, including changes that leave settled descriptors unchanged. Quote planning requires the current session/epoch, a slot and no prepared intent. The sender permits only its currently prepared intent; other prepared rows deny sending. Legacy complete-cut changes revalidate the whole union. Cache reuse does not waive the caller's live session/config/risk/account/market checks.
- The sender checks duplicate intent identity and its current slot through exact lookup instead of constructing/searching a historical tuple. Journal disagreement fails closed, with outstanding reservations and capital claims retained for reconciliation.
- A previously uninitialized, empty WAL remains usable for initial planning only. The gateway's durable identity claim, reservation, WAL-before-send/fsync and final network checks remain required. This change adds no migration, archive pruning or automatic rearm.
- The implementation holds only the journals' in-process locks during its synchronous check; no writer file lock is held across an `await`. This is not a cooperative-history or whole-campaign memory proof.

## TDD and regression

The recorded Red command ran 35 cases against unchanged baseline production code: **21 failed / 14 passed / 0 skipped**, six existing Pydantic warnings, **18.92 seconds**. Seven failures establish historical materialization/reiteration; six expose coherent historical session/epoch/side conflicts; eight establish missing independent memory, uncertainty, corruption and stale-writer verification at this local gate. An earlier probe had an unreached ACK assertion corrected to the fixture's actual `SEND_UNKNOWN` state before the recorded Red.

Fourteen additional negative/boundary cases bring the final new suite to **49 cases**. The suite includes:

- Actual protected maker dispatch and final network validation with `all_records()` guarded on the class, at every frozen history size in both legacy and archived journal formats.
- A 1k settled proof followed by active WAL/reservation updates, with settled iteration guarded, including a session mismatch and reservation unknown transition.
- Coherent historical WAL or reservation changes, missing/orphan rows, partial terminal checkpoints, identity aliases, memory divergence, uncertain state, changed journal binding, stale writer and same-size/same-stat corruption in both journals.
- Aborted intents with absent, terminal or unsafe open reservations; explicit cold restore; current-versus-foreign prepared intent handling; terminal transition invalidation; mutation during cold proof construction; revocation at the final network boundary with claims retained.

Final focused verification: **396 passed / 0 failed / 0 skipped**, six existing warnings, **57.20 seconds**. It includes quote/executor/final-send, hedge, joint recovery, continuous runner, archive/lazy integrity, inventory/exit, spot-session replay and executable acceptance tests. The new suite is mandatory in O.8.4 and its mapped A-cases; a gate regression proves it cannot be removed.

## Measurements

Six sequential fresh processes ran the unchanged settled-history workload: timing and cProfile/I/O/GC modes at 0/1k/10k. Each report pins identical current sources and checks they stayed unchanged. Setup/migration is excluded from hot-path profiles. Each configuration has one sample; host variation and profiler overhead prevent a causal elapsed-time speedup claim.

### Timing diagnostic

| Settled intents | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 8.5 | 87.5 | 93.9 | 11.2 | Pass |
| 1,000 | 226.3 | 621.0 | 634.8 | 41.0 | Fail: loop lag |
| 10,000 | 2,441.3 | 5,867.4 | 6,051.3 | 219.3 | Fail: qualification/send/loop lag |
| Frozen ceiling | 2,000 | 1,000 | 500 | 250 | Unchanged |

### Profiled filter/dispatch/final-send

At **every** profiled history size, WAL `all_records()` calls fall from **5 to 0**. The new proof runs five active coverage checks and no settled coverage scan within this stage: proposal has already established the settled proof. Complete historical coverage is still established on cold use or a settled-cut change; it is not replaced with pending-only checks. Historical proposal action/runner scans are outside this stage and remain.

At 10k, the sender stage records **9,225.7 ms** wall and **8,986.0 ms** process CPU, with **8,808** counted reads returning **1,036.3 MiB** to Python. Prior profile observations were 12,976.4 ms, 8,683 reads and 1,043.3 MiB. The read count increases because the proof independently verifies both journals; exact-byte `verify_maps` calls rise from 112 to **122**. These are returned bytes, not physical disk traffic. No historical corruption check is skipped.

Remaining cumulative costs overlap: attribution readiness takes approximately **2,995 ms**, financial `reserve` **2,409 ms**, two financial archive reloads **1,209 ms**, and exact segment verification **875 ms**. Fsync is approximately **2.25 ms**. Financial/provenance decode, freeze and validation remain substantial; zero tuple materializations does not imply zero historical work or constant memory.

## Full scale matrix and remaining work

The unchanged 16-case suite uses **1× frozen budgets**, with **8 passes / 8 failures / 0 skips**, **65.09 seconds**, and 21 warnings (six Pydantic plus fifteen pytest/JUnit property warnings). Thirteen cases produce timing samples; both 10k worker cases fail before the final-send sample, and the terminal no-recheckpoint regression has no timing sample.

Failures:

- Legacy 10k: send, loop lag and cancel-start ceilings.
- Segmented 1k: loop lag; segmented 10k: qualification, send, loop lag and cancel-start ceilings.
- Segmented/thread 10k and segmented/process 10k: `JOINT_HISTORY_WORKER_TIMEOUT`.
- Segmented/process 1k: loop lag. The thread 1k sample passes in this run; the previous matrix failed thread 1k instead. Single samples do not establish why scheduling differed.
- Segmented/journals 1k: loop lag; segmented/journals 10k: qualification, send and loop lag.

The mandatory 10k journal sample records **5,916.0 ms** propose → final send, **6,084.9 ms** maximum loop lag and **222.3 ms** cancel start. The separate timing diagnostic above is retained and does not replace this sample or any failed format/backend case.

Next work is verified financial/attribution state reuse and financial writes, then bounded worker/watchdog scheduling. Exact historical-byte reads, proposal action/runner history, whole-campaign memory, future settlement/cancel/recovery storage reservations and the earlier full-run carry timeout remain unqualified. Full candidate regression, both pinned coverage comparisons and the completion gate were not rerun. O.8.4/O.8.7 remain open; no aggregate offline completion is claimed.
