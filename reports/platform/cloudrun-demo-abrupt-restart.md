# Cloud Demo abrupt restart and stale position checkpoint

2026-11-09, Asia/Ho_Chi_Minh. **Current state: revision57 is HALTED after an
ownership lock timeout during the22:10 rollout.** All101 recorded fills match
individual OKX trade/order IDs, quantity, price and original fees; pending venue
orders0/0. One spot position aggregate is stale. The initial mitigation passed
89 PostgreSQL and37 deployment-review tests. A guarded automatic-migration
follow-up is implemented in source below; it is not deployed and no
live data/HALT has been changed. See the [current incident and recovery proposal](#rollout-lock-timeout-at-2210-local).

Earlier read-only check at22:02: revision56 was RUNNING,
control/ack16/16, no HALT/error;100/200USDT limits remain active.
It auto-deployed the documentation commit `bebf29c79…` at21:46; this is a planned
rollout, not evidence of an unresponsive engine restart. The earlier revision55
600-second observation below retains its own scope and pass.
Audit39 preserves all PnL peaks and financial/credential table fingerprints.
Google Start16/16 retains Until stopped.115 samples and70 REST-matched fills
(two new natural spot fills) support this bounded result; startup warnings and
perpetual inventory suppression remain explicit.
The earlier revision54 attempt below remains a failed600-second gate with a
separate15USDT risk stop; its record has not been relabelled as a pass.

Earlier operator update: revision54 (`hummingbot-demo-00054-7k6`) served100% traffic
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
- [x] Repeat the600-second continuous-run observation on revision55 with separately
  approved100/200USDT policy:115 samples pass, final RUNNING16/16 and70 REST-matched
  fills. The earlier revision54 attempt still fails its15/25USDT drawdown policy.
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
The Demo YAML and current operating guide contain those values; catalogue
validation and15 existing profile tests pass. The operator has now deployed
revision55 (`0acdcba89…`); actual `/profiles` confirms200/100 with2vCPU/1GiB.
The historical15/25USDT breach evidence remains unchanged. The operator separately
approved the [bounded recovery](../demo/cloudrun-drawdown-policy-recovery-proposal.md),
applied as audit39 after fresh68-fill REST proof, quiescence and full PostgreSQL16
backup/restore rehearsal. The new policy is bound, the old risk latch acknowledged
and all peaks/financial history retained. Google Start on revision55 succeeds;
the new600-second observation passes, without relaxing inventory caps. The
connected result above retains the distinction from the earlier failed run.

## Subsequent read-only follow-up —22:02 local

Current revision56 (`hummingbot-demo-00056-qwj`) is ready with2vCPU/1GiB and the
same live session/control16/16, no pending recovery or runtime error. Spot has
two acknowledged SELL orders; BUY allocation/skew and perpetual inventory-limit
reasons remain visible. Daily reporting is fresh and records92 fills (63 spot,
29 perpetual), latest spot fill21:59:32. This is recorder reporting, not a new
individual REST reconciliation of all92 fills.

Drawdown at the status read is13.7728USDT for spot against100, and15.0255USDT
globally against200. The latest available memory minute mean on revision56 is
45.94% of1GiB; CPU mean is5.11% of2vCPU. Revision55 rose from42.11% to47.23%
over27 available memory samples before rollout; revision56 rose from43.05% to
45.94% over16 samples. This does not establish a plateau or rule out a long-run
leak, though current memory remains well below the configured limit.

Since21:19:52, retained logs contain no application ERROR, child-unresponsive or
failure-restart event. Three isolated probe warnings are about301ms:21:20 on55,
21:46 and21:55 on56. Later status remains healthy. Do not describe the interval
as uninterrupted execution of one process across the normal21:46 deployment.
The check used only reads and changed no service, session, credentials or DB state.

## Rollout lock timeout at 22:10 local

This is a new incident during revision56→57 overlap, distinct from the original
revision52 stall above. Revision57 uses the same image `bebf29c79…`, environment
and2vCPU/1GiB resources as56. Its creation audit is22:09:24.022.

At22:10:01.472, PostgreSQL backend2564549 cancels the physical ownership SELECT
on `bot_state` with **lock timeout**. At22:10:01.496 the recorder's
`OrderCancelled` event106 fails with `OwnershipLost`; the service then stops,
cleanup cannot complete and later event callbacks encounter the persistence
failure latch. Revision57's reconciliation correctly refuses to resume unresolved
effects at22:10:06.011. These ERROR records are a causal chain, not independent
venue submission errors. HTTP/startup Ready does not mean trading recovered.

Direct inspection of the deployed registry image config confirms
`ENABLE_AUTO_MIGRATION=true`, without a revision-level override. Startup replays
all packaged scripts. Even `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an
AccessExclusive table lock. A new migration can wait for the old recorder's
RowShare table lock, while the recorder's next ownership SELECT queues behind
that DDL. The owner lock timeout then closes submissions and prevents graceful
checkpoint completion. The migration advisory lock only serializes migrations.

The real-PostgreSQL regression reproduces this sequence using each shipped
Cloud startup preset: **3 Red failures with OwnershipLost →3 Green passes**
after disabling runtime migrations. The deployed flag, rollout timing and exact
DB failure support this mechanism. The historical blocking SQL/backend PID was
not captured; that last attribution limit remains explicit. No evidence here
establishes an OKX transport/authentication error or an OOM event.

The initial mitigation sets all three Cloud presets to explicit
`ENABLE_AUTO_MIGRATION=false`; the private deployment env now has the same
override. Its first deployment-review change rejects enabled or omitted flags
(older images can inherit `true`). Its2 Red cases pass after that check. All37 review
tests,89 related PostgreSQL tests and pre-commit checks pass. Required migrations
remain an explicit `make migrate` with writers stopped; runtime validates the
existing schema. No ownership fence, timeout or risk threshold is relaxed.
These tests do not constitute a new image/load or Cloud rollout acceptance.
The subsequent requested guarded-runner implementation below supersedes the
blanket rejection of `true` for new qualified images; explicit policy remains required.

### Financial readback and exact proposed maintenance

Read-only independent REST reconciliation matches **101/101 fills:72 spot,
29 perpetual**. There are no missing venue fills since the reviewed68-fill
baseline within the verified REST window. Spot BTC cash changes equal recorded
fill quantities minus base fees. Pending SPOT/SWAP orders and creation-pending
intents are zero. Perpetual LONG0.0030/SHORT0.0048 BTC and both durable aggregates
match all recorded fills; they need no repair. Strategy risk latch remains
`_is_stop_triggered=false`, with no drawdown breach and unchanged100/200 limits.

Only spot aggregate `bae1325b-551f-4b50-b4f9-bfe6a1f79ef8`, controller
`demo_btc_spot`, side SELL, is behind the immutable ledger. Native `PositionHold`
replay and `_load_position_from_db()` round-trip produce this read-only preview:

| Field | Stored | Proposed from101-fill ledger |
| --- | --- | --- |
| amount BTC | 0.02749641 | 0.04152284 |
| volume_traded_quote | 2045.102469834 | 3095.093632178 |
| breakeven_price | 74377.07212810690559240279004 | 74539.54575790095282499944609 |
| realized_pnl_quote | 0 | 0 |
| cum_fees_quote | 1.6360819758672 | 2.4760749057424 |
| unrealized_pnl_quote | −13.96619301599999999999999992 | 0 at reconstructed average entry |

The last field is an explicit reconstruction mark; runtime must mark at fresh
market prices. It does not reset PnL peaks or assert a profit. Spot quantity is
strategy-relative net SELL inventory, not an account short or its full BTC balance.

**Proposed, not applied; separate incident-specific operator review required:**

1. Quiesce all service writers and independently repeat venue/ledger checks.
2. Take a private full PostgreSQL16 backup and restore-rehearse the bounded
   transaction/native startup comparison on an isolated clone.
3. Set only Cloud `ENABLE_AUTO_MIGRATION=false`, retaining image, credentials,
   BOT_ID, CPU/RAM and all other environment settings. Keep writers stopped
   throughout maintenance; require the explicit override before startup.
4. Under exclusive ownership/expected-state checks, replace only the spot
   aggregate fields above, remove only this incident's initialization/unclean
   HALT/reason and append a maintenance audit. Abort on unexpected drift.
5. Preserve all431 orders/101 fills, both perpetual rows, funding history,
   credentials/bindings, session configuration, risk policy, stop flag and peaks.
6. Restore min/max1, verify startup, then use the operator's fresh Google session
   for normal Start. Observe Demo quoting/fills and a controlled revision handoff
   with migration disabled before closing the incident.

Previous financial/drawdown recovery approvals do not cover this incident.
The [maintenance rule](../../docs/postgres-runtime.md#ownership-and-unresolved-exchange-effects)
requires an explicit operator-reviewed database change. Until that review,
**keep HALT; no live repair, restart request or Cloud env change has been made**.

- [x] Identify the first PostgreSQL error and distinguish cascading recorder errors.
- [x] Inspect the actual image's inherited startup flag and compare revisions.
- [x] Reproduce the DDL/recorder ownership failure; verify source/preset remediation.
- [x] Reconcile101 individual REST fills, cash, pending orders and hedge legs.
- [x] Prepare the exact one-row native replay preview and incident-specific proposal.
- [ ] Obtain review, quiesce/backup/restore-rehearse and apply the bounded recovery.
- [ ] Verify the no-DDL rollout and subsequent connected Demo run on Cloud Run.

## Guarded automatic migration — source follow-up

At the operator's request, `ENABLE_AUTO_MIGRATION=true` now has a coordinated
source implementation. The runner records filename/checksum/application time in
`hbot_schema_migrations`, atomically with each SQL file, and skips applied files.
Changed checksums fail instead of replaying altered SQL. A second runner rereads
history after obtaining the migration lock, preventing duplicate application.

Before all DDL, including first history-table creation on an old untracked DB,
the runner must obtain the existing physical-session trading owner lock. An
active owner causes compatible read-only standby without queued DDL. After the
old owner releases, the new service completes migration before attempting trading
ownership. An incompatible old schema fails startup and needs maintenance.
Explicit migration also refuses pending SQL while a trading owner is active.

`migration_pending` identifies standby; `migration_in_progress` identifies the
bounded background DB step. Responsive health allows only the runner's existing
180-second bootstrap deadline during that step; trading readiness remains false.
The wait is cleared afterward. Normal running-engine health budgets are unchanged.
The supervisor can still detect an unresponsive event loop independently.
While waiting for the old owner, one short advisory-lock transaction replaces
repeated full-schema scans. This is only a scheduling hint; migration still
reacquires its physical lock and validates before trading admission.

TDD retained8 initial PostgreSQL Red failures, one standby Red, one deployment
policy Red and one explicit health-wait Red. The new real-process handoff test
passes with supervisor, API and engine children on disposable PostgreSQL: new
startup succeeds in standby, the old owner stays unchanged, history remains
absent until old shutdown, then history is adopted and the next owner starts
without HALT. This test never contacts Cloud Run or OKX.

Cloud presets remain conservatively false; deployment review accepts explicit
true or false for a tested image. The actual deployed old image still requires
the false override and the separately reviewed financial recovery above.
No live configuration, financial row, credentials or HALT has been changed by
this source implementation. A new image/full load/Cloud rollout acceptance is
not implied by native and PostgreSQL results.

Source verification results:

- Entire CLI suite: **1,257 passed**, 7 dependency deprecation warnings.
- Full PostgreSQL attempt: **584 passed / 1 failed** in974.33s. The failure was
  the old performance-index fixture deleting indexes after marking their script
  applied, then expecting automatic replay. That attempt remains failed.
- Corrected pending-index fixture plus the new recorded-drift rejection contract:
  **2 passed**. Only the test fixture/coverage changed for this correction;
  the runtime correctly rejected drift. A full PostgreSQL rerun after that
  test-only adjustment was not performed.
- Final migration/bootstrap/real-process handoff contracts: **17 passed** after
  the bounded health wait and lightweight polling changes. Pre-commit passes.
- The Docker upgrade assertion now requires additive DDL after old-owner exit.
  No new Docker image/full E2E/load or real Cloud rollout was run for this change.
