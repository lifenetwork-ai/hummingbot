# Demo Spot percentage skew: connected verification

Observed 2026-11-18, Asia/Ho_Chi_Minh (UTC+07). CI deploys exact commit
`ca1b3c7119cc6485dcaefee69f372d95ae0ac6e8` as `hummingbot-demo-00171-9ph`,
Ready with 100% traffic. The resolved image digest is
`sha256:7417b976d1bd7f3f21bb6b276c3c153069f09b7340da22fa38eb52d23fe95abf`.

## Approved policy applied through normal Switch

Fresh process/owner identity matches revision 171 and Demo before mutation.
The old running session retains catalogue `2e4c3995…`; deploying alone does not
change its multiplier policy. A full catalogue comparison proves the new
`51344236…` catalogue differs only at `markets.spot.inventory_skew`: enabled,
target_base_pct=0.5, inventory_range_pct=0.02. All other profile, budget, cap,
drawdown, outflow and Perpetual reduction settings match the pinned catalogue.

Versioned API Switch `31c33145-088d-486b-8bce-138edab28ce2` succeeds at
control/ack 99, session `2601d796-13fa-4f79-b13b-283a6a866b9d`, both markets,
fixed follow_market, until_stopped with no expiry. Its Basic-admin accepted audit
is verified through the normal audit API. The new session's pinned catalogue hash
matches `/profiles`. No manual SQL, HALT reset, credential/config-environment
change or direct exchange order/close call is used.

## Short running observation

Between 03:21:03 and 03:24:02 local, 18 samples span approximately
179.95s. All samples remain fresh, running/no HALT on revision 171/control 99;
startup/live/ready return 200 throughout the sampled window. All 18 Spot samples
observe active portfolio_pct sizing with half-width 0.02 and two working BUY plus
two working SELL orders. Initial BTC weight 50.2413% gives approximately
BUY 0.8793 / SELL 1.1207, rather than suppressing BUY at this small imbalance.

One new recorded Spot SELL fill is observed at 03:23:18 local: 0.00044186 BTC at
64,033.9 USDT. No new BUY or Perpetual fill is observed in this short window.
Perpetual remains two closing SELL quotes at 72,938.67 with BUY inventory-limited;
LONG 0.0498 BTC and its entry price match before/after. Both credential slots remain
Connected. Bot is left running; no Stop is sent.

## Retained limits and diagnostics

The bounded revision 171 application-log read contains no ERROR, child restart,
service-stop or traceback entry. Startup/Switch diagnostics still include one
approximately 300.9 ms probe timeout, two loop stalls/recoveries (maximum 398.2 ms),
and four slow-stage diagnostics. These precede the sampled running window; this
feature is not evidence that the latency issue is resolved.

The deployed frontend bundle `/assets/index-DBy-if2N.js` contains percentage-band
and legacy-multiplier labels. This is artifact verification, not a signed-in
browser acceptance run. The local feature qualification remains 286 backend and
755 frontend tests, plus schema/type/lint/build/hooks checks.

This closes the approved policy-application and short quote-observation scope.
It does not close 30-minute two-sided coverage, sustained fill quality, Perpetual
breakeven-fill handoff, load/soak or production acceptance gates.
Private receipts/samples/proofs: `build/inventory-skew-percent-postdeploy-20261118`.

## Later deployment check: Overview reporting

At 10:25 local, Cloud Build `7e480427-836e-4070-af33-b752e14ec0e0` successfully
deploys `925aa384fae98b18dfd753d12314987b1c3b814a` as revision `00172-w24`,
Ready with 100% traffic. The resolved OCI image label independently matches the
commit; digest `sha256:0e1fb55483e3209213f77b3072e056e1b6a313c5e4c581cfd374907ceb893011`.
The deployed OpenAPI equals the committed specification. Frontend bundle
`index-C5r73G6S.js` contains the new planned-width and unverified-limit labels;
this is artifact verification, not an authenticated browser acceptance test.

The bot is **not trading** at this later check. A persisted technical HALT began
at 05:14:28 local on revision 171, before this deploy. Cloud logs show
`LockNotAvailable` while recording a BUY intent: the ownership `FOR SHARE` fence
waited for a lock on `bot_state` and timed out. Persistence subsequently became
unhealthy; unsent rejection could not finish durably, followed by a child restart
and bootstrap withheld. The transaction holding the conflicting lock is not
identified by these logs. No drawdown-policy increase is indicated by this cause.
`/startup` and `/live` return 200, `/ready` returns 503. During handoff the API
process reports revision 172 while the retained owner observation identifies 171.
By 10:32 both process and owner observations identify 172, fresh at control/ack
99, but the persisted HALT remains. This does not qualify trading or a
30-minute window on the new image.

The real `/status` check also exposes a gap in the Overview change: its SQL read
omits the archived catalogue, so direct full-row unit fixtures missed unavailable
quote budgets and controller market labels. A local follow-up retains only
bounded pinned policy fields in that same SQL projection, bound to session ID
and generation. A real PostgreSQL regression fails before the fix and passes
afterward; 65 unit and 10 PostgreSQL tests pass, including reporting isolation
and excluding a 500 KB unused catalogue payload. This follow-up is not deployed.

No Stop, Recover, Start, manual SQL, environment or risk-policy mutation is sent
during this read-only check. Deployment and incident evidence is retained under
`build/overview-postdeploy-20261118`; technical HALT recovery and running
qualification remain open.

## Local TDD remedy for prewrite intent contention

The ownership-row conflict is reproduced on disposable PostgreSQL: the old path
raises `LockNotAvailable`, poisons persistence and cannot continue admission.
The correction checks duplicate identity and acquires the initial `FOR SHARE`
fence with `NOWAIT` before staging any intent writes. Only that fence's `55P03`,
with the same transaction's confirmed DBAPI rollback, healthy connection and
previously healthy recorder, becomes temporary `IntentAdmissionBusy` readiness.
It releases only unsent local admission/reservations and backs off 500 ms before
fresh admission checks. It never replays a POST or resets an unhealthy latch.

The native OKX connector with fake HTTP and real PostgreSQL proves zero transport
calls and zero durable orders/attempts on contention, released cash reservation,
then one acknowledged send for a fresh identity after the lock clears. Duplicate
identity, post-fence write errors, lost/invalidated connection and failed rollback
remain fatal. COMMIT request loss and response loss retain closed send gates for
both possible durable outcomes.

Validation runs pass: 142 unit tests (plus five subtests), 134 PostgreSQL recorder/
ownership regressions, 24 focused admission/native lifecycle tests, and 14 focused
fault/protocol tests on the final implementation. These suites overlap and their
counts must not be added. Hooks and generated OpenAPI consistency pass. No new
environment flag or risk threshold is introduced.

This is local implementation evidence. It neither identifies the transaction
that held the historical lock nor changes the current Cloud Run HALT. Deployment,
verified recovery and connected running/coverage qualification remain separate.
Detailed RED/GREEN/fault receipts: `build/intent-contention-20261118`.

## Connected deployment and API recovery: revision 173

On 18 November, Cloud Build `db3531d8-f451-42a6-acc4-92703d092d19`
deploys `416a294edaba2171c0a8e4cd6ce3160424bce632` as
`hummingbot-demo-00173-ps8`, Ready with 100% traffic. The resolved OCI
configuration independently matches that commit; image digest
`sha256:e9c584d82c8e16003dc0939c5c3b4883b7c5ed3517e0404a8be984b69c912653`.
Both the API process and the fresh owner observation identify revision 173.

The authorized normal API flow stops control 100, checks the native evidence,
recovers to stopped/no HALT at control/ack 101, then starts continuous
`follow_market` at control/ack 102. Check verifies 360 fills in the anchored
scope and three canceled, zero-filled orders. Explicit review and confirmation
cover only those three terminal metadata repairs; no position repair is proposed
or applied. Native evidence is revalidated during Reset. The latest 100 recorded
fills are identical immediately before and after Reset. No manual SQL is used.
The configured Basic admin API identity and all recovery operations are audited.

Reset operation `6349f390-5f11-46e5-88d4-401f625e1fa4` completes at
11:04:14 local. Start command `3a7ff6ba-528c-4c9f-bc69-8ce72a014aaf`
succeeds with session `6263a104-fe0e-4068-9ee4-eda09bb6204d`, started at
11:06:44 local, `until_stopped` with no expiry. The pinned catalogue hash stays
`5134423679f1310f328c909910a364a1f06d769d57301877c62546179e2fc74d`:
100 USDT quote budget per market, drawdown 2,500/5,000 USDT, and all inventory
caps, percentage skew and close-first/breakeven policies are preserved.

Real contention recurs at 11:08:59 local on Spot and 11:10:48 on Perpetual.
The connector reports that no order was sent and admission waits for the
ownership row. The engine continues on control 102 without a HALT or child
restart in the bounded log reads. The Spot cooldown produces repeated retry
warnings; the Perpetual executor also emits an `Order failed ... Retrying` ERROR
for that local admission rejection. These messages remain visible as a logging/
retry-volume follow-up; they are not evidence of an exchange submission or a
new HALT. Two recorded Spot SELL fills at 11:08:10 confirm resumed trading.

Residual health evidence is retained: startup includes one approximately 301 ms
probe timeout and slow-stage diagnostics; a later `/ready` request returns 503
with no parsed JSON body. A subsequent fresh status remains running/no HALT,
and startup/live/ready all return 200. The source of that isolated HTTP 503 is
not established by the captured response and must not be attributed to the
engine or Cloud Run without further evidence.

The deployed `/status` now reports the pinned 200 USDT quote budget, market
labels and fresh account equity. Perpetual cap exposure remains explicitly
unknown when its engine cap assessment is absent; account LONG 0.0498 BTC is
separately observed. Capital allocation and unavailable baseline/transfer metrics
remain unknown. This does not qualify every Overview field or a 30-minute
two-sided coverage target. Private receipts and samples are retained under
`build/intent-contention-postdeploy-20261118`.

The completed follow-up window, 11:12:19–11:15:36 local, contains 19 samples
over 196.37 seconds. Every sample is fresh, running/no HALT on the same revision,
session and control/ack 102; startup/live/ready each return 200 for all 19 samples.
This follows the earlier isolated readiness 503 rather than excluding it from
the overall evidence. Spot working BUY counts range from one to two and SELL
from zero to two as inventory sizing and quote replacement change. The final
sample has two BUY and two SELL. Fourteen recorded Spot fills since Start include
eight BUY and six SELL. Perpetual retains two closing SELL quotes and observed
LONG 0.0498 BTC; no Perpetual fill is recorded in this window. Both credential
slots remain Connected. No Stop is sent; the bot is left running continuously.

An additional loop-stall diagnostic at 11:13:24 records a 254.17 ms heartbeat
gap and recovery approximately 116 ms after detection. Repeated admission
backoff/legacy executor retry logs also remain. The sampled availability result
therefore verifies that these observed contention events did not cause another
HALT; it does not establish permanent absence of technical HALTs, eliminate the
latency/retry-volume follow-ups, or close load/soak and production acceptance.

## Local follow-up: wait instead of consuming executor retries

Revision 173's connected log evidence shows why a second correction is needed:
one proven-unsent conflict starts a 500 ms cooldown, but each new quote attempt
during that cooldown still emits a failed-order event. Position executors consume
their retry budgets and prepare replacement IDs; managed closing order executors
also report an ERROR because their legacy classifier does not recognize the new
exception. The earlier implementation prevents the persistence HALT but does not
prevent this retry and log burst.

Four unit cases and four native/real-PostgreSQL cases fail before this correction.
The owner now waits within the original native create task, on the same client
identity, before any durable intent or POST. The shared 500 ms cooldown is outside
the admission slot. Every resumed attempt passes the normal current owner,
session, native-order, quote and risk checks; final-send checks retain their own
authority/priority. Task cancellation remains tracked and releases the slot.
Only `IntentAdmissionBusy`, already backed by the existing exact prewrite
rollback proof, takes this loop. Unknown COMMIT, post-fence failure, failed
rollback and lost ownership retain their existing handling.

Real PostgreSQL keeps a row locked across multiple fence attempts. Resumption
sends the same ID once; Stop, a removed tracker and changed quote each prevent
transport. Eight concurrent native waiters share the cooldown rather than
individually polling the row, allow loop callbacks to progress, emit zero
order-failure events, then produce exactly eight acknowledged sends with eight
distinct original IDs. Continuing contention warnings are summarized at most
once per 30-second interval, with INFO recovery after successful admission.
This is bounded lifecycle/stress evidence, not a load or soak result.

Four local HTTP tests initially fail for absent readiness diagnostics. The API
now marks health responses with `X-Hbot-Health-Source: api`; proxy failures return
distinct timeout, connection, invalid-response and HTTP-status reasons. Bounded
warning/recovery events record elapsed milliseconds without body/URL/exception
text. Real HTTP timeout/not-ready/invalid-response tests recover on a fresh
probe, leave API liveness available and perform no trading mutation. The 300 ms
proxy timeout and fail-closed readiness status are unchanged. These tests do not
identify the cause of the earlier historical 503 without captured source data.

Validation passes: 151 unit/executor tests plus two subtests, 54 health/process
tests, 28 PostgreSQL lifecycle/fairness tests, 38 PostgreSQL fault/send-progress
tests (including both network-at-COMMIT outcomes), and five final native
contention/burst cases. Suites overlap; counts are not additive. Hooks and
`git diff --check` pass. RED/GREEN receipts are retained under
`build/admission-cooldown-20261118`.

At 11:31 local the existing Cloud Run revision 173 is still fresh, running/no
HALT, control/ack 102 on the same continuous session. The new cooldown/health
implementation is local and is not part of that deployed image. No new cloud
mutation, environment flag, risk change or migration is performed for this
follow-up. Deployment, a single-image 30-minute window and release soak remain
open for the new source.

## Revision 174 observation and later status — 19 November follow-up

Cloud Build `89bdb6d0-cd08-4e2c-bfe5-287d63e7f4f2` deploys
`5350c50fa8bf8f276882cb95a2899790145fcc01` as `00174-jzc`.
The resolved OCI configuration matches the commit; image digest
`sha256:6baa20f543c603e836a9d5ceb63818872b698e1b8a0bd8c28067b7b6668913d5`.
Traffic reaches 100%; the owner and API both identify 174 by 11:43:33 local
on 18 November. The existing continuous session resumes at control/ack 102,
with an unchanged catalogue and no Stop/Reset/Start or manual SQL from this run.

The retained window is **incomplete**: 137 samples span 11:43:38–12:06:18 local,
22 minutes 40 seconds. Every retained sample is running/no HALT, with zero read
errors and all three sampled health endpoints returning 200. The process was
interrupted before its 30-minute completion marker; no completed 30-minute or
soak result is claimed. At the 20-minute log read, 14 bounded intent-contention
warnings are retained, with no legacy retry burst, ERROR or scheduled restart.
Nine internal probe timeouts and 31 loop-stall/recovery pairs remain in that
bounded log read, maximum heartbeat gap 522.98 ms. These latency findings are
not closed by successful sampled API health. INFO recovery-log coverage is
insufficient to count recovery events from logs alone.

At approximately 00:30 local on 19 November, the current service is instead
revision `00175-sf6`, commit `b1c573726ed7ba89f6bc1a9761fb27f09b02d504`,
with 100% traffic. A different `follow_token`/AVAX-reference continuous session
is observed at control/ack 103. No operator/session/configuration change is
overwritten by this follow-up.

The current state is **not normal trading** despite `halted=false`:
`reconciling_orders`, one quarantined uncertain Spot BUY, zero observed working
quotes in both markets, and `/ready` 503. API liveness/startup are 200 and owner
observations are fresh. The order is
`93027a12dac34fBCBBCUT919ab9bae18`, 0.00046433 BTC at 64,634.5 USDT.
Its only recorded status is PendingBuy from 15:59:18 local on 18 November,
with no exchange ID and zero recorded fills. The original error at 15:59:19
is POST HTTP 429 with OKX `50013` (system busy), followed by uncertain-outcome
reconciliation. Repeated native `51603` is not proof of non-admission or a
terminal cancellation. The automatic recovery view reports
`order_not_found_inconclusive` and `operator_required=true`.

A separate engine event at 00:19:31 local on 19 November records
`ownership_lost`, incomplete cleanup and a scheduled child restart. The captured
service failure message does not establish the underlying reason the ownership
session failed. The restarted engine remains in reconciliation. No new reset,
intent rejection, database maintenance or risk-limit change is performed;
uncertain-order resolution and the ownership-loss cause remain open.

Private evidence is retained in `build/admission-cooldown-postdeploy-20261118`,
including the interrupted samples, current API snapshots, exact order detail,
original POST failure and later restart logs. A new observation must use the
current image/session and begin after verified recovery; it cannot extend the
interrupted revision 174 window across this gap.

### Follow-up on order admission and ownership, 00:41 ICT

Revision 175 still reported control/ack 103, one isolated BUY and zero working
quotes on both markets. The active recovery policy reserved 30.0357467749080
USDT and allowed only funded Spot sells. The Spot SELL diagnostic in this
sample was `order_size_rejected`. The order status alone cannot establish which
of the size checks failed. Perpetual SELL showed submission pending.

The close-first controller has two proposal entry points. In a local replay
using the existing reduction-controller fixture with a long position and
active isolated recovery, `create_actions_proposal()` returned no actions.
Under the same conditions `determine_executor_actions()`, which the running
controller calls, returned both a BUY and a SELL create action. Its reduction
branch directly calls `_create_reduction_actions()` and skips the recovery-side
filter. Cloud Run recorded eight distinct Perpetual SELL IDs blocked by
`uncertain_order_risk_paused` between 00:19:28.691 and 00:19:30.089. These
were local submission blocks. This replay does not establish what caused the
database connections to reset.

Engine restarts occurred at 00:19:31 and 00:38:15 on the same revision. Cloud
SQL recorded eight “Connection reset by peer” messages for the Demo database
in each incident window. The first group began within 30 ms of the first
application ownership error in both cases. The connector logged the generic
`ownership_lost` and the later service-level log recorded
`ownership_already_lost`. These records do not identify the original physical
owner connection by PostgreSQL backend PID or which component initiated the
reset. The restart also restarted the operator escalation timer for the same
unresolved order.

The proposed regression test should exercise the controller tick with Spot
and Perpetual enabled, an ambiguous BUY and repeated not-found reads. It
should assert zero forbidden create actions and duplicate POSTs, allow a
funded and correctly sized Spot SELL, and preserve cancellation and late-fill
processing. A separate connection-loss test should preserve the first owner
failure diagnostic, the original incident age and the reservation across
takeover. Recovery of the current order needs account-bound order and fill
evidence. An incomplete history or an ambiguous outcome must retain the
reservation.

### Local remediation and exchange evidence

Both reduction proposal entry points now check recovery before reserving a
Perpetual level. The controller tick regression also checks that a funded Spot
SELL remains eligible and that Perpetual CLOSE proposals resume after recovery.
Ownership loss retains the first safe reason, PostgreSQL SQLSTATE when present,
connection invalidation and the old backend PID. A successor carries forward
the escalation timer when a pending creation still needs proof.

Scoped recovery can now consult a bounded, authenticated OKX Spot order-history
page after a 51603 detail response. It verifies a matching terminal order both
around the fill read and against the original durable identity and account
binding before applying the proof. An empty page keeps the reservation. The
existing admin status includes the pending order ID for operator drill-down.

Two read-only scans of the bound OKX Demo account returned zero Spot open
orders and 51603 for `93027a12dac34fBCBBCUT919ab9bae18`. Each scan read
199 historical orders and 118 fills in the scoped interval. Neither contained
that client order ID. The scans are saved in the ignored
`current-native-proof-private.json` evidence file. There is still no positive
terminal evidence for the live incident. The runtime changes were tested
locally. No production data or order state was changed.

### Proposed Demo recovery for the outstanding BUY on revision 176

The read-only preflight checked the active OKX Demo account and verified both
the credential fingerprint and the durable account bindings for
`93027a12dac34fBCBBCUT919ab9bae18`. The journal stores uncertain HTTP 429 /
OKX 50013 with a transport deadline of 15:59:23.767 ICT on 18 November.
PostgreSQL has one pending order, one pending tracker entry, one PendingBuy
status, zero target fills and no exchange order ID for this target.

Two scans of the Spot BTC-USDT account from 15:58:18 on 18 November through
01:19:20 on 19 November returned zero open orders, 51603 for the exact ID and
no target entry among 1,586 historical orders per scan. All 118 venue fills
matched the DB in each pass by client order ID, trade ID, side, amount and
price. The fill reconciliation tests for financial effects in this window.
The order history and exact-ID lookup supply no positive terminal status.

An operator can review the following scoped maintenance. Use Stop and drain
the writer. Take a recovery snapshot, refresh the exact native and DB evidence,
then obtain the ownership advisory fence. Abort on changed bindings, an ACK,
a target fill, another unsettled intent, an unrelated recovery operation or a
risk stop. In one transaction mark exactly this pending order locally failed,
close its submission attempt as rejected after operator review, remove its
zero-fill tracker entry, and append one audit entry. Preserve the original
receipt, order history, fills, positions and risk checkpoints. Restore the
service and use Start only after verifying the cleared obligation. Verify the
active owner, pending count, readiness and working orders on allowed sides.

The three metadata edits passed rehearsal on disposable PostgreSQL with the
captured rows. ACK drift, receipt drift and reapplication were rejected.
The service drain, audit insertion and production start remain pending review.
Private evidence is in `recovery-preflight-176-private.json` and
`rehearsal-176-private.json` under the ignored postdeploy evidence directory.
The live database was queried read-only.
