# Demo 50013 intent recovery — 2026-11-13

Status: operator-approved maintenance applied, cold restart and Start verified;
Demo is left trading. Revision130 serves commit5bfd54cc0 at100%.

## Cause and evidence

At13:13:05 UTC (20:13:05 ICT), Spot BUY
`93027a12dac34fBCBBCUT92a50b642b4` attempted0.00070835BTC at69163.8USDT.
The durable submission attempt is `uncertain`, receipt
`{"code":"50013","http_status":500}`, with a five-second request deadline.
There is only a PendingBuy status, no exchange ID and no local fill for this ID.
This predates revision130. The old revision's shutdown also failed on this ID.

Two authenticated, GET-only native scans verify the bound Demo account. Each
finds zero pending Spot orders and zero Spot fills from13:12:05UTC onward;
local Spot fills in the same interval are also zero. Each complete Spot order
history scan returns two other orders, neither the target. Both exact-ID reads
return51603. The retained receipt contains no correlated positive expiry rejection.
Neither the50013 response nor repeated51603 alone is permission for automatic
retirement. The documented request deadline prevents late processing; it does
not retrospectively establish the outcome of this request.

The runtime remains paused during startup reconciliation, without a durable HALT.
Its manual recovery API rejects unresolved creation intents. The existing
PostgreSQL test `test_repeated_not_found_does_not_retire_an_uncertain_native_order`
passes. No runtime code change or blanket50013 rejection is proposed.

## Exact maintenance scope for operator review

1. Stop Demo through the authenticated admin API and drain/stop the physical
   writer. Recheck revision130, account binding, terminal evidence and DB values.
   Acquire the existing ownership advisory lock before maintenance.
2. In one transaction, update only the target Order's `creation_pending` to false
   and `last_status` to `OrderFailure`, and SubmissionAttempt's state to `rejected`.
   Preserve the original50013 receipt, immutable intent/context and all history.
   This is an operator-reviewed absence reconciliation, not an invented venue ACK.
3. Remove exactly that ID from the Spot saved tracker (`market_states.id=1`).
   Keep the other tracker entries and both other market-state rows unchanged.
4. Record one maintenance audit naming the real operator approval and proof
   identity. Do not impersonate a Google principal. If stopping creates a technical
   shutdown fence for this same intent, clear only that verified technical fence
   after drain. Any risk/drawdown/policy stop or unrelated evidence aborts.
5. Restore the original service scaling/image/resources/env. Cold-start with the
   preserved financial/runtime checkpoint, then Start Demo via the actual admin
   principal and verify native orders, fills, health and logs.

Recorded baseline counts:29,698 orders,5,849 fills,3 positions,93,683 order-status
rows and19,342 submission attempts. No fill, position, fee, financial hash,
credential, cap, drawdown limit or peak is repaired/reset. Recheck counts under
the drained writer; any financial drift requires a new review. No lifetime
ledger deletion, new submission under the old ID or forced Reset is authorized
by the proposed transaction.

## Rehearsal and limitations

A disposable PostgreSQL14 clone containing the implicated order, attempt, saved
tracker and target fill scope accepts the three scoped updates. ACK drift,
receipt drift and repeat application reject atomically. It verifies zero local
Spot fills in the reviewed interval. The clone/container was removed. The full
service stop/start and final audit transaction are not yet rehearsed/applied;
revalidate the complete production preconditions before applying.

Private evidence: `build/cloudrun-51603-20261113/` (`order-proof-private.json`,
`native-proof-private.json`, `rehearsal.json`, `unknown-outcome-test.txt`). Secret
material is not included in this report. All production DB access was read-only.

The requirement is [PostgreSQL runtime operations](../../docs/postgres-runtime.md):
“Those cases still require an explicit operator-reviewed database maintenance
change after independent reconciliation.” This case has an unresolved submission
outcome; the automatic recovery API deliberately cannot approve this maintenance.

## Applied and verified

The operator explicitly approved this proposal in the conversation. Fresh native
proof repeated both scans before maintenance. Stop succeeded at control/ack87,
then service-level manual scaling temporarily stopped writers. The maintenance
transaction acquired the physical ownership advisory lock, required a released
owner, stopped control87, no HALT/pending mutation/risk stop and unchanged exact
intent/receipt/tracker terms. It required zero local Spot fills in the reviewed
interval and the approved baseline counts.

The transaction committed audit278 (`submission_intent_reconciled`, maintenance
actor `operator-approved-20261113`). Only the target order's pending/status fields,
its submission-attempt state and its saved tracker entry changed, plus one audit.
The original50013 receipt/context were preserved. Complete table hashes verified
all other tables unchanged, including all5,849 fills,3 positions, risk/runtime
checkpoints, credentials, order-status history and financial journal. Hashes of
every unrelated order and submission attempt also matched. No HALT or risk reset
was needed. The first preflight attempt failed ORM mapper initialization before
any mutation; the corrected transaction has one committed application receipt.

Original automatic scaling min1/max1, revision/image/env/resources and traffic
were restored. A separate Start using the actual Basic admin principal succeeded
at control/ack88, session `949a8fde-fa9b-4311-bbb8-aba1cadf0ced`, preserving
`both`, random profile selection and `until_stopped`. No Google identity was
impersonated. Eighteen samples from15:08:10 to15:09:40UTC are all running,
ready200 and no HALT. The15:09:43UTC API view has Spot1 BUY/2 SELL and
Perpetual0 BUY/2 SELL. A separate native pending-order snapshot confirms actual
post-only Spot/Perpetual orders; counts vary between independent reads.
Ten post-Start Spot fills match native trade IDs, client IDs, quantities, prices
and original fee currency/amount exactly.

The bounded15:07:50–15:09:42UTC log query has no application ERROR or scheduled
restart. Three probe timeouts and three loop-stall/recovered pairs remain. A
separate earlier ERROR is an HTTP503 `/ready` request while the service was
stopped, not an engine exception. This is recovery/start qualification, not a
30-minute quote or zero-probe-latency pass. Perpetual BUY is still inventory-cap
blocked. The bot remains running and the latency/availability architecture
follow-up remains open.

Receipts and immutable hashes: `applied-private.json`,
`before-maintenance-private.json`, `scaling-*-private.json`,
`start-receipt-private.json`, `running-samples-private.jsonl`,
`native-after-start-private.json`, `new-fill-proof-private.json` and
`final-start-logs.txt` under the existing private evidence folder. The temporary
SQL proxy is closed after the final native verification.
