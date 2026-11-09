# Cloud Demo abrupt restart and stale position checkpoint

2026-11-09, Asia/Ho_Chi_Minh. **The API is available; trading is HALTED.**
Revision53 contains the filled-entry handoff correction, but startup reconciliation
blocks strategy admission. Its actual-Demo handoff acceptance remains open.
[Sanitized evidence and exact proposed row changes](cloudrun-demo-abrupt-restart-evidence.json).

## What the deployment timeline establishes

| Local time | Revision | Observation |
| --- | --- | --- |
| 17:38:31.695 | 52 | Supervisor reports11 failed probes and15.145s without recovery |
| 17:38:34.983 | 52 | Engine exit `-9`; restart scheduled |
| 17:39:00.113 | 52 | Restart rejects restored position quantities that differ from OKX |
| 17:42:51.819 | Deployment audit | First `ReplaceService` for revision53 |
| 17:44:25.282 | 53 | Revision becomes Ready |
| 17:44:31.945 | 53 | The same position mismatch blocks startup |

The first unresponsive event precedes the new deployment request by about4m20s.
Revision52 was created at16:40:49.327; its later Ready/traffic transition is not
its creation time. The new deployment did not initiate the first engine failure.
It inherited the unresolved recovery state. The handoff correction was absent
from revision52, and revision53 has not admitted the strategy to exercise it.

## Resource pressure and remaining uncertainty

The deployed limit is1vCPU/512MiB. Memory minute means rise from96.45% at17:31
to97.26%,97.35%,97.57% at17:37–39. CPU utilization also rises. Continuous CPU
allocation is already enabled; request-idle CPU throttling is not the explanation.

Increase memory to **1GiB** for the next controlled Demo run, retaining the current
CPU/image/configuration for comparison. Google supports1GiB with1vCPU; a resource
update creates a new revision. [Cloud Run memory configuration](https://docs.cloud.google.com/run/docs/configuring/services/memory-limits).
This provides headroom, not proof that the original stall was caused by memory.
The retained logs do not establish an OOM kill, a memory leak or a unique cause
of the15-second unresponsive interval. No resource/probe threshold was changed.

## Financial readback

Read-only PostgreSQL plus independent OKX REST establish:

- All65 recorded fills match individual venue trade/order IDs, exact base
  quantities, prices and original fee currency/amount. There are306 order rows.
- Pending venue SPOT/SWAP orders are0/0; durable creation-pending count is0.
- Perpetual LONG0.0030 BTC and SHORT0.0048 BTC equal the signed recorded OPEN/CLOSE
  fill totals. Durable perpetual position aggregates still contain0/0.
- The spot aggregate still contains an older net SELL0.00133901 BTC; all recorded
  spot fills net to SELL0.01933873 BTC. This is strategy-relative inventory,
  not a claim that the spot account is short or that its full cash balance equals
  that quantity.

The fill ledger survived. The aggregate checkpoint is behind it. Archived
executors do not themselves make the native position-hold checkpoint current:
`store_executor()` archives executors, while `store_all_positions()` persists
held-position summaries during graceful strategy shutdown. A hard kill can
prevent that shutdown from completing. Startup's exact perpetual comparison
correctly refuses to assume that the old zero quantities match the venue.

The strategy checkpoint retains `_is_stop_triggered=false`; the incident does
not justify resetting drawdown peaks, risk configuration or any other stop flag.

## Reproduction and scope

`test/integration/postgres/test_mm_abrupt_restart.py` uses a real child process,
the native recorder and an isolated PostgreSQL database. After two fills commit,
the child is killed with SIGKILL:

1. Without the new position checkpoint, fills/original fees remain durable and
   the native startup comparison rejects the stale quantities.
2. With the matching checkpoint written first, the same kill preserves quantities,
   volume and fees, and the native comparison succeeds.

The two new cases plus two initial-position integration cases pass: **4 passed**.
This reproduces the fill/checkpoint failure boundary, not the original15-second
stall. It is regression coverage of recovery behavior, not a Red/Green runtime
fix or evidence that memory pressure has been eliminated. No trading guard was
relaxed and no live financial data was changed.

## Proposed recovery — not applied

The read-only preview replays each immutable fill once, in timestamp/trade-ID
order, through native `PositionHold`, grouping perpetual OPEN/CLOSE fills by
hedge leg. All original fees occur exactly once. Restoring the resulting rows
through native `_load_position_from_db()` reproduces the proposed quantities.
This is an in-memory preview, not an applied database repair or a transactional
restore rehearsal.

| Existing position row | Amount before → proposed | Fees USDT before → proposed |
| --- | --- | --- |
| Perpetual BUY | 0 →0.0030 | 0.0313583934 →0.0444956874 |
| Perpetual SELL | 0 →0.0048 | 0.0313583934 →0.1025962296 |
| Spot SELL | 0.00133901 →0.01933873 | 0.0799991516752 →1.1488062101952 |

The evidence includes row IDs and every changed aggregate field: amount, volume,
average entry, realized/unrealized PnL and fees. Old aggregate totals are replaced
by the replay, not incremented again. Original trade rows are untouched. The
preview marks unrealized PnL at reconstructed average entry, explicitly zero;
runtime must obtain a fresh market mark. Funding remains in its separate ledger.

Before applying, require a fresh venue/ledger readback and unchanged row
fingerprints, stop all writers/owners through the normal service maintenance
path, and take a restore-reviewed backup. A consistent private21-table raw
PostgreSQL JSON snapshot is retained separately; it contains encrypted envelopes
and is not a schema-complete `pg_dump` restore rehearsal.

The reviewed transaction would update only the three aggregate rows, remove
only this incident's HALT/reason and append one maintenance audit. Preserve all
orders, fills, funding, credential envelopes/versions, account bindings, session
intent and strategy risk checkpoints. Roll back on any unexpected baseline or
venue discrepancy. Do not clear HALT while leaving its quantity mismatch.

Separately update Cloud memory to1GiB, retaining1vCPU, the image and other
configuration. Resume only after reconciliation succeeds, then verify actual
Demo fills and the handoff branch, pending orders, risk exposure and memory.
These live actions require operator approval; none has been performed here.

## Checklist

- [x] Compare first failure with both deployment timelines.
- [x] Independently reconcile all recorded fills and current hedge-leg quantities.
- [x] Reproduce SIGKILL with stale/matching checkpoints on disposable PostgreSQL.
- [x] Prepare exact native replay preview and preserve original financial history.
- [ ] Obtain operator review and perform the bounded live repair/resource change.
- [ ] Verify successful startup and real-Demo handoff on revision53 or its successor.
- [ ] Attribute or reproduce the original unresponsive interval; retain the
  distinction between resource headroom and a proven runtime fix.
