# Partial-close and database-failure cleanup E2E

This extends native managed-credential lifecycle acceptance using the unchanged
shipping entrypoint/image, isolated PostgreSQL, an independent REST/WS simulator,
local KEK and audited Basic bootstrap. It does not contact or change real OKX
Demo/Live, Google or GCP resources.

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
outage. Native image acceptance of these two fault boundaries is still pending.

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
Final rebuilt-image acceptance must pass before this runtime defect is closed.
