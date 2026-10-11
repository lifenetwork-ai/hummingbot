# O.8 WAL/reservation journal checkpoint — 2026-10-11

## Verdict

**WAL/reservation disk partitions and explicit cold restore are implemented. O.8.4 and O.8.7 remain open.** Active manifests have an explicit byte bound; logical restoration still materializes retained history and integrity checks still read all referenced segments. This checkpoint does not establish bounded RAM, campaign latency, future-settlement capacity, demo eligibility or live permission.

The earlier [archive](LIFE_OKX_O8_ARCHIVE_EVIDENCE_2026-10-11.md) and [worker](LIFE_OKX_O8_WORKER_EVIDENCE_2026-10-11.md) reports describe their historical source snapshots. Current outcomes and source hashes are recorded in [journal results](evidence/life_o8_journal_results.json).

## Implemented boundary

- `IntentWAL.migrate_archive(policy)` publishes an explicit schema-2 manifest. Only `TERMINAL` and `ABORTED_BEFORE_SEND` identities move to immutable settled segments; prepared, unknown and acknowledged identities remain active. Intent/wire tombstones, session/epoch, reservation identity, slot identity and cancellation evidence are retained.
- `ReservationLedger.migrate_archive(policy)` separates terminal reservations, their fills/fees, and cash-flow/joint-event deduplication from pending reservations and their fills/fees. Balances remain in the active checkpoint. A fully filled order without terminal proof remains active. Historical fill quantity, fee identity, currency and signed amount still pass the ordinary financial decoder on cold restore.
- `IntentWAL(path, archive=policy)` and `ReservationLedger.restore(path, limits=limits, archive=policy)` require explicit format/policy selection. Archive path lists and all capacities are caller supplied; wrong paths, mismatched limits/policies or omitted policies are rejected. No controller/YAML factory automatically migrates or restores archived journals. Runtime integration can receive explicitly restored journal instances through its existing injection APIs.
- Every manifest binds the journal kind, limits where applicable, archive policy, segment identities and coverage. Publication fsyncs segment files and their directory before atomically replacing/fsyncing the manifest. Old and orphan segments are retained. Migration is atomic **per journal**; it is not a coordinated transaction across all financial journals.
- Legacy and archived stale writers cannot overwrite a migrated/advanced cut. Reservation writes now compare the exact previously observed file bytes under a writer lock, including legacy writes. An ambiguous manifest replacement fences the current instance; a fresh process verifies either the complete old cut or complete new cut.
- Runtime permission, attribution, economics, telemetry, hedge and SWAP checks carry the selected format. `verified_disk()` returns sealed decoded evidence only after current exact manifest/segment checks. A warm unchanged permission check does not cold-decode the journal again. Replacing/disabling archive binding is rejected; file timestamps do not grant trust. Financial memory comparisons and cross-journal provenance checks remain in place.
- The legacy reservation worker decoder does not accept an archive manifest as a complete snapshot. Archived ledgers use the normal segment verifier; worker injection cannot publish manifest-only financial evidence.
- Capacity refusal does not publish a partial manifest, discard deduplication evidence or release pending claims. Unreferenced orphan allocation blocks new risk; unchanged settled branches still permit cancellation bookkeeping. Future settlement space is **not reserved** yet: a settlement that requires more segments can fail closed and retain its pending evidence. This is a remaining delivery requirement, not a settlement-throughput guarantee.

## TDD and verification

The initial new suite recorded **16 failing tests** before migration APIs existed. A warm-permission regression then failed before decoded WAL evidence was reused; two archive-binding regressions failed before disabling archive checks was fenced. No timing threshold was widened.

The mandatory journal suite covers explicit migration/restore, terminal/pending partition ownership, historical intent/wire reuse denial, fill/fee/cash-flow deduplication, balances and pending remainder, stale legacy/archived writes, same-stat corruption, missing/symlinked segments, duplicate or misclassified partitions, active/archive capacity refusal, six killed-process migration cuts, ambiguous publication, and actual protected send/cancel with and without a thread worker. Cancel ACK without terminal status retains both the pending reservation quantity and shared-capital claim. The warm permission regression forbids another cold decode and then corrupts a segment while preserving its timestamps; permission is denied.

The final focused run passed **203 tests, 0 failed, 0 skipped**, with six existing warnings, in 14.47 seconds. It includes all **38** new journal cases plus worker, joint recovery, verified-state, reservation persistence, acceptance, risk, WAL, attribution and controller telemetry regressions. Python lint/import/format and whitespace/key hooks passed.

The full candidate passed **2,172 tests and failed 5**, with no skips and 27 warnings, in 261.61 seconds. All setup/teardown phases passed. Every failure is a 10k growing-history case: legacy, segmented, segmented/thread, segmented/process and segmented/journals. The candidate uses the existing frozen 5× instrumentation headroom for latency measurements; worker refresh deadlines are unchanged. Coverage was collected, but failures prevented both pinned changed-line comparisons and the completion summary. No earlier coverage percentage is reused.

## Isolated scale measurements

The unchanged full matrix passed **9 cases and failed 7**, with six warnings, in 85.38 seconds. Legacy/segmented 10k cases exceed latency budgets; both 10k worker variants time out before final send. The process 1k and journal 1k/10k cases also exceed isolated latency limits. Timing varies between runs; this checkpoint does not establish an overall throughput improvement. The mechanism regression proves removal of repeated cold decode on unchanged warm permission checks, not bounded total historical work.

| Format/backend | Completed intents/fills | Joint poll ms | Propose → final send ms | Maximum loop lag ms | Cancel start ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy | 0 | 4.5 | 52.1 | 59.2 | 11.4 |
| Legacy | 1,000 | 110.6 | 250.1 | 253.9 | 93.3 |
| Legacy | 10,000 | 1532.9 | 3794.4 | 3910.1 | 863.2 |
| Segmented | 0 | 6.3 | 81.0 | 87.6 | 11.7 |
| Segmented | 1,000 | 113.8 | 372.8 | 390.5 | 56.9 |
| Segmented | 10,000 | 3067.0 | 6133.8 | 6665.1 | 1506.7 |
| Segmented/thread | 0 | 28.8 | 203.1 | 207.0 | 14.2 |
| Segmented/thread | 1,000 | 217.6 | 407.9 | 426.2 | 62.3 |
| Segmented/process | 0 | 20.7 | 79.0 | 78.4 | 11.1 |
| Segmented/process | 1,000 | 257.0 | 1266.6 | 1278.9 | 53.4 |
| Segmented/journals | 0 | 7.7 | 92.6 | 99.1 | 13.2 |
| Segmented/journals | 1,000 | 146.2 | 576.9 | 756.2 | 82.0 |
| Segmented/journals | 10,000 | 1811.0 | 6707.5 | 6927.6 | 547.6 |
| Segmented/thread | 10,000 | timeout | not reached | incomplete | not reached |
| Segmented/process | 10,000 | timeout | not reached | incomplete | not reached |

Unit policies are explicitly synthetic: two records per segment, 8 KiB per segment, 256 KiB archive allocation, 64 segments and 16 KiB active manifest. The complete-history fixture explicitly supplies 500 records per segment, 1 MiB segments, 64 MiB archive allocation, 512 segments and a 256 KiB active manifest. Smaller fault-test policies deliberately exercise refusal. None is a live default. Threshold bytes and mandatory selectors remain in the acceptance manifest/results; raw phase outcomes are retained in [regression evidence](evidence/life_o8_journal_regression.json).


## Remaining O.8.4 delivery

1. Bound decoded RAM and verification/permission work across WAL, reservations, attribution, loss/subsidy/exit budgets, capital/joint state and action/executor ownership. The disk partitions alone do not satisfy this.
2. Reserve storage for pending intents' future settlement and cancellation/recovery writes before granting new risk; no implicit live values or evidence deletion can supply that headroom.
3. Make historical integrity/provenance validation incremental or cooperatively bounded, with exact journal/account/runner/session/config/risk/clock fences at publication and final send. No file lock may span an await.
4. Pass every unchanged 0/1k/10k full-history protected-send, heartbeat and watchdog cancel gate, then run O.8.7 completion and both pinned coverage comparisons. Retain the legacy, segmented, thread and process variants; the new `segmented-journals` variant is additional mandatory evidence.
