# Partial-close and database-failure cleanup E2E

This extends native managed-credential lifecycle acceptance using the shipping
entrypoint, isolated PostgreSQL, an independent REST/WS simulator, local KEK and
audited Basic bootstrap. A confirmed recovery defect required a rebuilt image.
This work does not contact or change real OKX Demo/Live, Google or GCP resources.

## Final result and candidate

**Eight final-image E2E cases pass:** four new partial/DB-fault cases plus all four
previous managed lifecycle cases. Native release regression passes **2,689 tests
plus 11 subtests**. Final full PostgreSQL confirmation passes **452 tests**; the
initial full-run probe timeout and a separate runner startup failure are retained
below, not overwritten as passes. Six additional runner checks pass separately.

- Runtime source: `16b268737`; fixture foundation: `509b34c9c`.
- Canonical `Dockerfile --target cloudrun`, Linux amd64, tag
  `hummingbot-cloudrun:cleanup-recovery`.
- Image: `sha256:89d5e7c61fcf811f830fe250ba92760edde52887acc62fa9c1e817efe0a6d3be`.
- Manifest: `sha256:33c374fa6983c618cd94065828259eb0de5156f1620e2be0200a47afba4e6de3`.
- Size: 1,374,436,993 bytes, within the existing 1.5 GiB budget.
- Runtime module SHA256 in the image and checkout both equal
  `2d95dca80c4297f1b0b6ffb2f4f351f7efa95a28737aa82d7d8d797e5f1f39c0`.
- [Portable evidence](admin-cleanup-fault-evidence.json) retains image/fixture/host
  hashes, exact outcomes, failed attempts, regression logs and image cleanup.

| Final new case | Exact venue/recorder fills | All physical close attempts | Result |
| --- | ---: | ---: | --- |
| Partial live close plus WS loss | 6 / 6 | 2 | Read back 40%, wait for original 60%, no extra close while live |
| IOC-like partial cancellation plus WS loss | 6 / 6 | 3 | One new residual 60% close after terminal/position proof |
| Cleanup-fill SQL failure | 5 / 5 | 2 | Atomic rollback; ordinary process replacement and one API Retry recover same root |
| Final removal-audit SQL failure | 5 / 5 | 2 | Ciphertext/receipt roll back; automatic retry completes once after fault removal |

All cases verify exact original fees, flat managed perpetual exposure, preserved
spot/companion credentials, no rejected duplicate close, safe audit and subsequent
restart/removal. Counts are independent simulated ledgers, not actual OKX trades.
Forced WS loss and recorder/database faults retain expected ERROR logs and the
failed recorder process exits; these are intentional fault windows, not evidence
of zero errors or uninterrupted trading during a database failure.

Final new group: `build/e2e-offline/20261106-153445-540333/result.json`.
Previous lifecycle group on the same new image:
`build/e2e-offline/20261106-153445-e58454/result.json`.

## Partial closes

Three fixture Red checks exposed the missing partial-close policy and validation;
all are Green with 22 related fixture checks. The one-shot stimulus interrupts
actual simulator WS connections, fills 40% of the first native market close, and
either leaves it live or returns an IOC-like cancelled terminal state. Later closes
use normal native connector submissions and independent venue positions.

Both targeted image cases pass:

- Still-live partial: REST readback commits exactly the first 40%; credentials and
  receipt remain pending, with only one physical close attempt. Replayed trade ID
  is ignored; the original remaining 60% fills, then the other hedge side closes.
  Six exact venue/recorder fills/fees, two physical closes, flat perpetual, retained
  spot/companion and selected-key removal survive restart.
- Cancelled partial: after terminal readback and fresh position proof, the same
  operation submits exactly a new 60% residual close and the opposite hedge leg.
  Six exact fills/fees and three physical closes; no rejected/duplicate send,
  over-close or early credential erasure. Subsequent restart preserves final state.

The partial checkpoint and all physical order terms are retained as test evidence.
The previous full-fill cleanup oracle keeps its original defaults; these cases
explicitly require different send terms/fill counts rather than weakening it.

## Targeted PostgreSQL faults

Two new real-PostgreSQL tests first fail for the missing fault helper, then pass
with actual SQLSTATE `40001` exceptions at cleanup fill insertion and the final
successful Disconnect audit insertion. The latter occurs after the key-erasure
statement within the same transaction, proving rollback retains ciphertext and
the applying receipt; disarming permits exactly one successful completion.

The fill fault rolls back fill, position reduction and fees together and marks the
recorder unhealthy. Test code never flips that health flag to force recovery.
The injector rejects databases outside the explicit `hbot_test_` namespace. Its
function/sequence live in a separate test schema; sequence increments survive
rollback and prove the intended SQL statement reached the injected failure.
Disarming drops only the fault schema/trigger, never changes financial or recovery
rows. These are targeted transaction failures, not evidence of a whole-server
outage. Both boundaries now pass on the rebuilt shipping image. No SQL position,
fill, key or HALT repair is used to make recovery succeed.

## Confirmed recovery defect and TDD fix

Original-image native fill-failure attempt
`build/e2e-offline/20261106-151728-624919/result.json` safely rolled back the fill
and retained ciphertext. After ordinary process replacement and API Retry, the
remaining close was locally blocked, returning `cleanup_failed`. The old process's
`shutdown_incomplete` fence survived even after the replacement read back the close
fill and matched exchange/held positions. This is a runtime recovery defect, not a
failed matching assertion or permission to bypass the close gate.

Six initial PostgreSQL Red cases reproduce the approval/fence mismatch. The fix
retires only a prior-generation process shutdown fence during fresh scoped cleanup
approval: frozen checkpoint, earlier approved generation, inactive runtime, no
pending account mutation, zero reported and actual unresolved orders, and the
existing fresh terminal/account/position proofs are required. Risk HALT/reason,
owner/control/key/deadline/final-send checks remain intact. Two additional negative
regressions cover a locally unresolved intent and missing prior approval.

One separate fixture defect produced blank/zero last-trade fields when republishing
a filled status. Its Red test proves the wrong terminal trade/fee identity. The
fixture now retains the actual last fill fields; no production fee parser is changed.
The original failed fill-DB case passes on rebuilt image `89d5e7c6…` at
`build/e2e-offline/20261106-153107-fd6d10/result.json`, then passes again in the final
four-case group. The recovery defect is closed under that explicit tested scope.

## Regression qualifications and retained failures

| Check | Outcome / log |
| --- | --- |
| Full native release regression | 202 + 11 + 1,723 + 753 = 2,689; 11 subtests; `/tmp/hbot-cleanup-native-final.log` |
| Focused PostgreSQL proof/fault/authority checks | 30 pass; `/tmp/hbot-cleanup-shutdown-green-final.log`; overlaps full PG total |
| Initial full PostgreSQL run | 451 pass / 1 fail in 917.39 s; `/tmp/hbot-cleanup-postgres-final.log` |
| Unchanged full PostgreSQL confirmation | 452 pass in 768.35 s; `/tmp/hbot-cleanup-postgres-confirmation.log` |
| Isolated unchanged probe check | 1 pass / 451 deselected; `/tmp/hbot-cleanup-probe-isolated-confirmed.log` |
| Runner publication/timeout/invalid-address regressions | 6 pass; `/tmp/hbot-postgres-port-final-green.log` |

The initial full-run failure was
`test_observer_ownership_round_trips_fit_private_probe_deadline`: an HTTP
`TimeoutError` against its existing 300 ms deadline while SQL checks deliberately
sleep 120 ms. Its stopped observer does not exercise the modified cleanup approval
branch. No probe/runtime timeout or threshold is changed; the unchanged full rerun
and isolated test pass. Timing alone does not establish its exclusive cause, so it
is not labelled a fixed probe bug or silently discarded.

An extra isolated invocation did not reach pytest: Docker briefly returned no
published port, and the runner's immediate split raised `IndexError`.
`/tmp/hbot-cleanup-probe-isolated.log` is an infrastructure failure, not a skipped or
passed check. Two Red tests reproduce the port-publication race and timeout; a
bounded loopback-only publication wait fixes it. Four additional negatives reject
invalid/non-loopback mappings. This changes only test startup, not application
budgets. The subsequent real isolated invocation succeeds.

One initial build invocation was interrupted to correct its commit label before
acceptance. The final image label, module hash, ABI/JIT/TLS and dependency discovery
are verified; no partially built/mislabelled image is used as acceptance evidence.

## Image housekeeping

Removed **10 unique unused Hummingbot images**, with their obsolete local-registry
tags/digests, using explicit unforced removals. Every running/stopped container
reference is preserved, including the existing Demo and incident snapshots. Keep
the new `89d5e7c6…` candidate and `1bb946c3…` rollback; PostgreSQL, registry and other
projects are untouched. Volume names and container counts remain unchanged by the
image cleanup. Detailed IDs are in the portable evidence.

Docker image accounting changes 17.42 GB to 10.92 GB. Build cache is deliberately
retained and its accounting increases correspondingly; this is not a claim of
6.5 GB of physical disk space reclaimed. No volume/container/build-cache prune runs.

## Reproduction and remaining scope

```sh
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:cleanup-recovery \
  --scenario managed-cleanup-faults --trading-env demo
python scripts/test_e2e_offline.py --image hummingbot-cloudrun:cleanup-recovery \
  --scenario managed-lifecycle --trading-env demo
```

The four new cases are included in the standard `--scenario all` matrix. These two
targeted groups retain `release_scope_complete=false`: no new full release/upgrade
or sixty-minute load/retention gate is claimed on this rebuilt image. Earlier long
load evidence keeps its original image scope. Whole-server outage and lost COMMIT
acknowledgement are not established by the targeted SQL exception tests. Recorder
recovery explicitly includes a normal process replacement and versioned Retry;
it does not promise seamless trading through a database failure. Real Google/GCP
and a reviewed managed-key cutover of the existing Demo ledger remain deferred.
