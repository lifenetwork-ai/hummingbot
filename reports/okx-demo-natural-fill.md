# Natural-fill Demo follow-up — 2026-10-30

**Acceptance complete: real fills/fees verified, reviewed aggregates repaired,
and real filled-exposure restart passes on the fixed image.**
[Evidence](okx-demo-natural-fill-evidence.json).
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
Real Demo recovery also passes on this image after the operator-approved repair
below. Both markets resume four post-only quotes after active restart; session ID,
generation, anchors and deadline remain pinned. The original six fill rows are
byte-equivalent and remain unique through three exchange/DB readbacks. There were
no additional fills during the fixed-image retry: natural fill observation belongs
to image715a, and the repaired filled-exposure restart belongs to imagee526.

## Reviewed recovery applied

The operator approved the temporary preset, two-row repair, explicit HALT clear
and fixed-image retry. We reconstructed only the two overwritten aggregates from the private
pre-test durable snapshot plus the five independently matched OPEN fills, using
the existing `PositionHold` arithmetic. Preserve the prior durable cost basis,
all order/fill/executor history and the spot aggregate.

| Aggregate | Before repair | After repair | Accumulated USDT fee | Cost basis USDT/BTC |
| --- | --- | --- | --- | --- |
| Long | 0.0081 | 0.0084 | 0.0047181294 | 78549.67821428571428571428571 |
| Short | 0.0022 | 0.0027 | 0.007868270 | 78571.72222222222222222222222 |

The repair passed on a disposable clone restored from the post-failure backup:
two conditional updates, exact expected quantities, unchanged fill ledger and
recovery/HALT state, and stale repair replay rejected. After a fresh independent
OKX readback and private backup, the reviewed transaction updated exactly two real
Demo rows while all writers were stopped. Order/fill/executor fingerprints and the
spot aggregate stayed unchanged. Its full field diff is in the evidence JSON; private
SQL/backups remain outside Git. No exchange position is altered or closed.

Only this resolved HALT was explicitly cleared with stopped/acknowledged/no-owner
prerequisites. No automated HALT reset was introduced. This follows
[the recovery procedure](../docs/postgres-runtime.md). After real restart and Stop,
independent exact-decimal checks confirm the repaired quantity, cost basis, fees
and traded volume on both perpetual legs, plus the spot aggregate. Final perpetual
quantities also match OKX; fee-net spot cash remains exactly 1.00000598628 BTC.

The canonical service now uses fixed imagee526 and the original private catalogue.
API is up, trading stopped, no HALT, no pending mutations and no exchange orders.
All four ledger/position fingerprints remain unchanged across this final startup;
private original environment/catalogue hashes match. The fixed retry emitted no
ERROR/CRITICAL records. Previous715a remains stopped for forensics. No DB wipe,
blanket restore, live trading, push or Cloud Run deployment occurred.
