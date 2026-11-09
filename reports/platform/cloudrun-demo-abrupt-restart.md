# Cloud Demo abrupt restart and stale position checkpoint

2026-11-09, Asia/Ho_Chi_Minh. **Reviewed checkpoint recovery succeeded; trading is
now STOPPED/HALTED by a separate spot drawdown limit.**
Latest operator update: revision54 (`hummingbot-demo-00054-7k6`) serves100% traffic
with **2vCPU/1GiB**, confirmed at20:10 local. The image is unchanged from revision53.
Fresh REST shows pending SPOT/SWAP orders0/0 and the same long0.0030/short0.0048 BTC.
The resource update alone did not clear `RecoveryRequired`. Subsequent approved
maintenance repaired three aggregate rows and removed the incident HALT, audit36.
Google Stop13/13 and Start14/14 are separately audited. The attempted600-second
observation fails after89 healthy samples, the last at461.887s: spot breaches its
15USDT controller drawdown limit at20:31:01.025. The observer then issues a normal
Google Stop15/15. The new risk checkpoint/HALT remains; it was not reset. Two
natural full spot fills hand off before60s; perpetual new quotes remain blocked
by the existing gross cap. This is not a full load gate or a two-sided pass.

Revision53 introduced the filled-entry handoff correction. Its revision54
successor exercised two actual spot handoffs; perpetual coverage remains open.
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

The incident revision's limit was1vCPU/512MiB. Memory minute means rise from96.45% at17:31
to97.26%,97.35%,97.57% at17:37–39. CPU utilization also rises. Continuous CPU
allocation is already enabled; request-idle CPU throttling is not the explanation.

Increase memory to **1GiB** for the next controlled Demo run, retaining the current
CPU/image/configuration for comparison. Google supports1GiB with1vCPU; a resource
update creates a new revision. [Cloud Run memory configuration](https://docs.cloud.google.com/run/docs/configuring/services/memory-limits).
This provides headroom, not proof that the original stall was caused by memory.
The retained logs do not establish an OOM kill, a memory leak or a unique cause
of the15-second unresponsive interval. The investigator made no resource/probe
threshold change; the operator subsequently increased resources as recorded above.

## Pre-repair financial readback (65-fill baseline)

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

## Reviewed recovery — applied

The read-only preview replays each immutable fill once, in timestamp/trade-ID
order, through native `PositionHold`, grouping perpetual OPEN/CLOSE fills by
hedge leg. All original fees occur exactly once. Restoring the resulting rows
through native `_load_position_from_db()` reproduces the proposed quantities.
The operator subsequently approved the exact repair. A PostgreSQL16 custom-format
`pg_dump` was restored into an isolated local PostgreSQL16 container, and the same
three-row transaction plus native recovery comparison passed there before the
live transaction committed. No trading engine or KEK was loaded into that clone.

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

Fresh pre-transaction REST again matched65/65 fills, pending orders0 and unchanged
hedge legs. Google Stop was followed by temporary Cloud service-level manual
scaling0; the DB had no owner or other application clients. The repair transaction
also acquired the exclusive advisory lock. The custom-format backup and earlier
raw JSON snapshot remain private; both include encrypted credential envelopes.

Before the final transaction, checksum comparison caught different default
collation ordering between Cloud and the restored database, plus one normal
runtime event from a service restart. The first attempted transaction rolled back
without financial changes. Deterministic `COLLATE "C"` ordering, a fresh quiesced
backup/restore and matching table fingerprints were required before retry. Only
the old spot unrealized mark changed between the earlier proposal read and final
quiescence; the final preview records that value. Quantities, fees and source
fills were unchanged.

The committed transaction updated only the three aggregate rows, removed only
this incident's HALT/reason and appended maintenance audit36. All other table
fingerprints and bot-state fields, including strategy risk checkpoints, matched
before/after. Orders, fills, funding, credential envelopes/versions and account
bindings were preserved. Session control used normal Google-authenticated API
commands; no direct trading-intent override was used.

Autoscaling was restored to automatic min1/max1, retaining revision54 and2vCPU/1GiB.
The service became ready/stopped with no HALT/error before Google Start succeeded.
The new both/follow_market session is Until stopped. Observe actual Demo fills,
handoff, quote reasons, risk exposure and memory separately from startup success.
At baseline, gross perpetual0.0078 BTC leaves only0.0002 below the configured0.008
cap; new quote sizes do not fit, so both perpetual sides report `inventory_limit`.
No cap was raised and no old position was closed. Spot BUY is also suppressed by
configured allocation/skew; acknowledged spot SELL quotes remain present.

## Checklist

- [x] Compare first failure with both deployment timelines.
- [x] Independently reconcile all recorded fills and current hedge-leg quantities.
- [x] Reproduce SIGKILL with stale/matching checkpoints on disposable PostgreSQL.
- [x] Prepare exact native replay preview and preserve original financial history.
- [x] Verify the operator's resource update to2vCPU/1GiB on revision54.
- [x] Obtain operator review, restore-rehearse and commit the bounded live repair.
- [x] Verify startup without HALT/error and Google Start on revision54.
- [x] Observe two natural full spot entries handing off to native POSITION_HOLD
  before their configured60s refresh; keep the partial-fill case distinct.
- [x] Verify final68/68 individual REST fill matches, cash/fees/positions and zero
  pending orders after graceful risk Stop; retain the new drawdown protection.
- [ ] Complete the600-second continuous-run gate: this attempt fails on a genuine
  controller drawdown breach, not an observed engine kill/restart.
- [ ] Exercise perpetual full-fill handoff; its existing exposure cap blocks new
  quotes and was not overridden.
- [ ] Attribute or reproduce the original unresponsive interval; retain the
  distinction between resource headroom and a proven runtime fix.

## Post-recovery Demo — separate protective stop

The Google-controlled session `0830e903-81b9-4288-8b5a-91b8edb1cb6d` starts in
both/follow_market, Until stopped. Retained logs and final DB show the same owner
UUID/generation45; no engine replacement is observed. One startup probe warning
reports300.997ms, and one controller progress-confirmation warning reports5.376s.
The other quote warnings identify the existing perpetual inventory limit. There
is one ERROR incident, `drawdown_controller_limit`, not a zero-error pass.

At20:31:00.236, the last healthy sample reports spot peak2.5864965559712 USDT,
PnL−12.2037083869328 USDT and drawdown14.790204942904 USDT. At20:31:01.025, the
native strict `peak - current PnL > 15` branch triggers for `demo_btc_spot`.
It records the breach and `_is_stop_triggered=true`, revokes quote production,
then stops. This is the covered strategy-report drawdown from its retained peak,
not a claim that total account cash fell15USDT. The exact breach PnL value is not
retained after teardown; do not invent it from the earlier sample. The global
threshold is25USDT and was not the reported trigger.

The observer correctly marks its attempted600-second gate failed, then submits
normal Google Stop for its own session (control/ack15/15). The final phase is
HALTED/RecoveryRequired, reason `drawdown_controller_limit`; this is a different
HALT from the repaired position-checkpoint incident. Its checkpoint is preserved.
No threshold, peak, stop flag or new HALT was reset, and no perpetual position
was liquidated.

During the retained start/running window, eight available Cloud memory minute
means range **44.917–46.051% of1GiB**; CPU means range4.827–12.077% of2vCPU.
Both CPU/memory limits changed and perpetual quoting was cap-blocked, so this is
not a controlled performance comparison with the old run. It establishes current
headroom; it neither proves the old stall's cause nor rules out a long-run leak.

Financial final readback:

- **68/68** fills match individual venue trade/order IDs, base quantities, prices
  and original fees. Three new spot fills total0.00145960 BTC and0.0872787752376
  USDT fees. BTC cash delta matches exactly, and final durable spot quantity,
  volume and fees equal all recorded spot fills.
- The two full fills retain `entry_order_filled=true`, native held orders and
  POSITION_HOLD archives. Executor lifetimes are approximately11.7/27.7s versus
  60s configured refresh; closure timestamps (one-second precision) are about2s
  after the fills. Exact subsequent venue-order-to-level correlation is absent
  from these archives; this is not a claim of uninterrupted quoting or a measured
  end-to-end replacement latency.
- Final totals:323 orders,68 fills,3 aggregate position rows,230 archived executors.
  Pending SPOT/SWAP orders0/0; perpetual LONG0.0030/SHORT0.0048 BTC remain and match
  REST. Their average entries also match the native reconstruction.

A second consistent private custom-format backup retains all68 fills and the new
drawdown HALT/checkpoint. The pre-repair backup remains separately retained.
Restarting alone cannot clear this protective stop. Any further operation needs
a separate review of current exposure and drawdown policy; the approval for the
old initialization HALT must not be reused to reset this new risk breach.

## Subsequent requested Demo limits — source update only

The operator subsequently requested100USDT per controller and200USDT globally.
The Demo YAML and current operating guide now contain those values; catalogue
validation and15 existing profile tests pass. The historical15/25USDT breach
evidence above remains unchanged. This source edit has not deployed a new image,
changed the live risk checkpoint or cleared the current drawdown HALT. The new
YAML must be packaged, and the protected checkpoint requires separate reviewed
recovery before trading can resume.
