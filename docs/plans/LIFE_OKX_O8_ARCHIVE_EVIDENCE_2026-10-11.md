# O.8 archive foundation checkpoint — 2026-10-11

## Verdict

**O.8.4 and O.8.7 remain open.** This increment implements and tests the PolicyState storage foundation. It does not close the growing-history latency gate or establish offline completion, demo readiness, live permission or economic viability.

## Implemented scope

- `ArchivePolicy` requires explicit paths, records per segment, maximum segment bytes, total archive bytes, segment count and active manifest bytes. There are no live defaults. Archive creation and migration are opt-in API operations; the controller does not automatically migrate journals or infer capacities.
- Schema-1 journals remain supported. `PolicyState.migrate_archive(policy)` publishes a schema-2 manifest under the existing writer lock. Shared-capital and joint-recovery constructors accept `checkpoint_archive` for explicit restore with the same capacities. A legacy instance cannot overwrite a migrated cut; absent or different archive policy fails restoration.
- Immutable content-addressed segments precede the manifest: file fsync, segment replacement, segment-directory fsync, then atomic manifest replacement and manifest-directory fsync. A failure after attempted manifest replacement fences the current instance. Old and interrupted/unreferenced segment files are retained, never silently deleted.
- Restore checks exact segment bytes, SHA-256, entry counts, map-key uniqueness, configured paths, policy binding and resource limits. Warm views still reread the referenced bytes; timestamp/size equality cannot establish integrity. Reads of configured manifests and segments are size-bounded before decoding. Independently decoded immutable views are reused for unchanged branches.
- Synthetic integration archives capital `settled` tombstones and joint `bundle.orders`, then uses the actual protected sender and cancellation path. Cold constructors retain ownership, claims and the manual-rearm requirement.
- Total allocation checks include orphans. Capacity overflow denies allocation through the capital/joint new-risk gates. The old verified manifest remains readable, and unchanged archive branches permit bounded active clock/cancel bookkeeping. Cancellation does not release the pending claim. Tests exercise both capital and joint orphan overflow.
- WAL wire lookup now uses an index tied to a strong reference to its verified immutable record cut, shared by independently verified disk images. Mutable test/legacy maps are reindexed; changed cuts invalidate the index. Duplicate wire identities are rejected before becoming a verified cache entry.
- Carry allocation reads and reconciliation-due queries use read-only verified views. SWAP cancellation persists clock advances through shallow checkpoints rather than copying full joint history.

## Explicit synthetic configuration

The scale fixture uses 500 records per segment, a 1 MiB segment limit, 64 MiB total archive allocation, 512 files and a 256 KiB active manifest limit for each configured checkpoint. Small fault tests supply smaller capacities independently. These are offline fixture values, not calibrated campaign/live limits.

The existing 0/1,000/10,000 full settled-history test now runs both **legacy** and **segmented** variants. Each exercises actual propose/filter/final send, production watchdog, heartbeat and connector-entry cancel timing. The frozen 2,000/1,000/500/250 ms qualification/send/loop/cancel budgets are unchanged. No xfail, skip or higher ceiling replaces a successful final send.

## TDD and verification

The archive suite first failed collection because the storage module did not exist. Wire-index tests and read-only query tests then reproduced missing-index and mutable-history access failures before their implementation. Passing fault tests include actual child-process termination at segment publication and on both sides of manifest replacement; these simulate process death, not a power-loss experiment.

Commands and final observed counts/timings are retained in [machine-readable results](evidence/life_o8_archive_results.json). Prior history evidence and its source hashes remain historical; this report does not overwrite them.

Final focused suite: **48 passed, 0 failed, 0 skipped**. Full candidate regression: **2,091 passed, 2 failed, 0 skipped**; all setup/teardown phases passed. Both failures are the mandatory legacy/segmented 10k scale cases. The auditor collected coverage but stopped before pinned changed-line comparisons and a completion summary. Earlier coverage percentages are not measurements of this increment.

The final isolated run reports **4 passed, 3 failed**: both 10k cases and the segmented 1k event-loop ceiling. The latter passed under the full audit's declared 5× instrumentation headroom; that does not waive its isolated 500 ms ceiling. No rerun is selected to hide the isolated failure. Observed timings in milliseconds:

| Format | Completed intents/fills | Joint poll | Propose → final send | Maximum loop lag | Cancel start |
| --- | ---: | ---: | ---: | ---: | ---: |
| Legacy | 0 | 4.7 | 51.2 | 56.6 | 12.2 |
| Legacy | 1,000 | 96.8 | 216.9 | 222.7 | 63.7 |
| Legacy | 10,000 | 1340.3 | 3181.8 | 3281.8 | 621.0 |
| Segmented | 0 | 6.0 | 71.5 | 77.5 | 10.5 |
| Segmented | 1,000 | 95.3 | 319.1 | 765.7 | 47.3 |
| Segmented | 10,000 | 1266.7 | 3015.8 | 3179.0 | 302.0 |

Segmentation does not demonstrate lower authorization latency here. Its verified result is bounded, durable storage publication and restore; repeated synchronous full-history validation remains unfinished. Exact samples, source hashes, frozen budget/manifest hashes and the complete [regression outcomes](evidence/life_o8_archive_regression.json) are retained in the JSON evidence.

```sh
conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_checkpoint_archive.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_verified_state.py \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_acceptance.py

conda run -n hummingbot python -m pytest -q \
  test/hummingbot/strategy_v2/life_liquidity/test_o8_settled_history.py -s --tb=short

conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.o8_acceptance \
  --candidate --output-dir /tmp/life-o8-archive-final
```

## Remaining work

1. Apply a coordinated bounded active/archive format to WAL, reservations, fills/fees, attribution, loss/subsidy/exit budgets, and action/executor ownership. The current segments cover capital/joint branches only. Complete views are still retained in memory, within explicitly configured storage capacities; these limits are not precise decoded-process-memory budgets.
2. Define capacity reservation for pending intents' eventual financial/provenance settlement. Current capacity guards refuse an overflowing checkpoint and preserve claims; they do not promise space for every future settlement or implement archive rotation.
3. Move full-history qualification and verification into bounded cooperative work with version/account/runner/session/config/risk/clock fences before results are applied. No file lock may span an await. Exact-byte segment verification currently remains synchronous and proportional to referenced history.
4. Pass the unchanged latency gates for both formats and both objectives where budget-cycle history differs, then run the full completion audit and both pinned coverage comparisons. Candidate regression or storage fault tests alone cannot close O.8.
