# Natural-fill Demo follow-up — 2026-10-30

**Partial acceptance: real fill/fee readback passes; the original image fails
filled-exposure restart.** [Evidence](okx-demo-natural-fill-evidence.json).
The earlier [zero-fill protocol](okx-demo-current-image.md) retains its scope.

A bounded temporary private preset used 40 USDT per market, one level per side,
and base half-spread 0.0003 before the unchanged fee floor and volatility policy.
All guard/exposure policies, controller IDs and existing positions were retained.
Matching was natural on OKX Demo, with no taker orders or forced trades.

## Verified exchange evidence

One spot BUY filled 0.00025419 BTC at 78678.4 USDT, with original fee
0.000000203352 BTC. Five perpetual OPEN fills added 0.0003 BTC long and 0.0005
BTC short; original USDT fees total 0.0125863994. All six executions independently
report maker on OKX. Recorded IDs, exact prices/base quantities and original fee
currency/amount match REST. Three repeated reads retain six unique rows.

BTC cash is exactly 1.00000598628 after the spot fill and BTC-denominated fee.
Exchange perpetual exposure is 0.0084 BTC long / 0.0027 BTC short, matching the
pre-test baseline plus recorded OPEN fills. There are no pending exchange orders.

## Confirmed restart defect and TDD remediation

The original image re-applies `initial_positions` from YAML on every startup.
That hides valid post-fill PostgreSQL holds behind stale 0.0081 / 0.0022 seeds.
Reconciliation correctly HALTs on the mismatch, but failed-startup cleanup then
writes the stale seed amounts and zero fees into two aggregate position rows.
The immutable six-fill history and executor order details survive.

The minimal fix treats seeds as bootstrap-only for admin-managed MM controllers
that already have durable holds. It uses one batched startup read. Zero/partial
snapshots suppress seeds too; an unavailable DB propagates failure. The existing
upstream override behavior remains available to unmanaged strategies. No schema,
API policy, exchange reconciliation gate or trading engine is replaced.

Eight targeted Red tests produced six expected failures and two passes before
the fix. All eight plus 42 existing orchestrator tests pass afterward. Full native
regression passes 2,047 tests plus 11 subtests; PostgreSQL passes
268 tests. The new shipping-image E2E reproduces the same filled-restart HALT on
the original image. The same E2E passes on replacement image `e526118b…`, manifest
`566788f2…`: both-market fills, exact durable quantity/cost/fees, pinned session,
active restart, dedup and clean Stop. Runtime fix: `783d35c69`; E2E fixture:
`1ee10e01b`. The packaged strategy source hash matches the tested checkout.
This is targeted image acceptance, not a repeated full release/hour-load gate.
Real Demo recovery on this image still awaits operator review and remains open.

## Concrete reviewed recovery proposal

Reconstruct only the two overwritten perpetual aggregates from the private
pre-test durable snapshot plus the five independently matched OPEN fills, using
the existing `PositionHold` arithmetic. Preserve the prior durable cost basis,
all order/fill/executor history and the spot aggregate.

| Aggregate | Current stale quantity | Reconstructed quantity | Accumulated USDT fee | Cost basis USDT/BTC |
| --- | --- | --- | --- | --- |
| Long | 0.0081 | 0.0084 | 0.0047181294 | 78549.67821428571428571428571 |
| Short | 0.0022 | 0.0027 | 0.007868270 | 78571.72222222222222222222222 |

The repair passed on a disposable clone restored from the post-failure backup:
two conditional updates, exact expected quantities, unchanged fill ledger and
recovery/HALT state, and stale repair replay rejected. It has **not** been applied
to the real Demo database. Its full field diff is in the evidence JSON; private
SQL/backups remain outside Git. No exchange position is altered or closed.

Before retrying Demo: stop all account-writing
processes, recheck exchange truth, apply only the reviewed atomic aggregate
repair, and explicitly review clearing this resolved HALT. No automated HALT
reset is introduced. This follows [the recovery procedure](../docs/postgres-runtime.md).

The canonical service and original private catalogue are restored. API is up,
trading stopped, HALT retained, owner healthy. Neither a DB wipe nor a blanket
pre-test restore was performed; both would lose real fill history.
