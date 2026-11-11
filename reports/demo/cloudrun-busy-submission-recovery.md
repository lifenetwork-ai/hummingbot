# Cloud Run Demo busy-submission recovery

Status: operator-approved maintenance applied; Demo trading resumed and verified.
Scope: `hummingbot-demo`, OKX Demo only, revision85 / source d1f0894cf.

Stop succeeded at control/ack56. Check failed with `recovery_scope_limit`:
10,295 orders exceed the API's5,000-order bound. The shipping bound is unchanged.
There is exactly one pending intent and613 fills; the service remains stopped/HALT.
The API operations used the actual `basic:bootstrap-admin` principal; they do not
impersonate the Google account. The maintenance audit will name maintenance authority.

## Independent evidence

- Two complete REST fill scans per market match all613 local fills:355 Spot,
  258 Perpetual. Quantity, price, original fee token/amount and identity match;
  no missing, extra or duplicate fill was accepted by the proof.
- Account IDs match the active managed bindings. Both account pending-order lists
  are empty. The exact uncertain client ID returns51603; it has no fill anywhere
  in the complete verified window. Its original POST returned HTTP429/50013 hours
  earlier. This reviewed combination is not a generic one-GET absence rule.
- Native perpetual contract conversion matches the unchanged LONG0.0030 and
  SHORT0.0048 BTC checkpoints. No perpetual position is closed or repaired.
- The stored Spot position is proved as a native held-order journal prefix,
  starting from the previously audited261-fill repair. All prior fill records and
  account bindings remain identical. The exact stored amount, fee, volume, entry
  basis and realized PnL match after282 Spot fills. There are73 later Spot SELL
  fills.48 already have native held-transfer records;25 require terminal-fill
  roll-forward. All355 Spot fills are included exactly once in the proposal.

## Exact proposed changes

1. On order `93027a12dac34fBCSBCUT71e28ef85e0`, change only `creation_pending`
   from true to false after reviewed absence reconciliation. Preserve order/history,
   existing OrderFailure status and null exchange ID; do not invent an acknowledgment.
2. Remove only that same failed order from `market_states.id=1.saved_state`, so
   a cold connector does not restore the obsolete pending tracker entry. Preserve
   every other saved order and the other two market-state rows.
3. Update one Spot aggregate, position `bae1325b-551f-4b50-b4f9-bfe6a1f79ef8`:

| Field | Before | After |
| --- | --- | --- |
| side | SELL | SELL |
| amount |0.11619787 |0.16440497 |
| volume_traded_quote |10922.707656565 |14503.853868841 |
| cum_fees_quote |8.7381661252520 |11.6030830950728 |
| breakeven_price |74621.09528061673448260725164 |74523.04234446633111659893664 |
| realized_pnl_quote |-5.718631957716833234514687103 |-5.718631957716833234514687103 |

   This Spot SELL aggregate is the bot's net sold quantity, not a new short/margin
   order. It creates no trade. Unrealized PnL is reconstructed at average entry
   (zero), then recalculated from fresh marks at Start; no live PnL is fabricated.
4. Clear only technical `halt`, `reason`, `shutdown_incomplete`; set
   `runtime_active=false`. Keep session stopped at control56, all policy/peak/risk
   stop/outflow data, credentials, bindings, funding and histories unchanged.
5. Insert one scoped maintenance audit with plan/backup hashes and preserved counts.

All10,295 orders,613 fills and30,440 order-status rows remain. Both perpetual
checkpoints remain byte-equivalent. No financial history is deleted or backfilled.

## Backup and rehearsal

Read-only full DB backup after Stop:1,388,037 bytes, SHA256
`5691b7088a22a1b37d8cdd13352aebe94fc21797555349bf6a30c3585bd8b92e`.
The dump restores on a disposable loopback-only PostgreSQL16 clone.
Rehearsal passes with all histories/credentials/unrelated table hashes preserved;
position drift, a drawdown HALT and applying twice each reject atomically without
partial changes. Plan SHA256:
`4e0eee7194e47f7cf9f4ac693776915b79b0ae8d05b5384a6cc5b3ad70e9ae93`.
Private raw evidence is in `build/mm-busy-resume-20261110/`.

## Application sequence

After operator review, recheck the exact revision, stopped control56, fresh full
venue proof and current DB values. Temporarily stop the Demo service's writers
using service-level scaling, preserving the revision/image/env/resources/traffic;
confirm owner release and acquire its advisory lock. Apply all changes in one
transaction, preserving row counts and immutable-table hashes. Restore original
scaling, verify healthy stopped/no HALT, then explicitly Start the same approved
Demo profile/markets and risk limits. Observe actual pending orders/logs and
reconcile any new fills. Any drift, new order, risk stop or failed proof aborts.

The requirement for this separate review is
[PostgreSQL operations](../../docs/postgres-runtime.md): unsupported history needs
an explicit operator-reviewed database maintenance change after independent
reconciliation. Check/Recover API does not authorize this repair automatically.


## Applied result

The operator approved this exact proposal in the session. After a fresh double
venue proof, the Demo writers were stopped through service-level manual scaling;
physical ownership was released before the advisory-locked maintenance transaction.
All changes committed atomically, audit177. The original scaling was restored to
AUTOMATIC, min1/max1; revision85, image, environment,2 CPU/1 GiB and traffic were
preserved. Runtime risk checkpoints and both perpetual aggregate rows were unchanged.

A separate Start by the actual Basic admin succeeded at control/ack57, using
`both`, `fixed`, `follow_market`, `until_stopped`. No Google identity is impersonated.
At14:41:16–14:43:06 UTC, twelve samples all have startup/live/ready200, running=true,
HALT=false. Spot has two working SELLs in ten samples, one in one sample, and none
in one pending-executor sample. Independent native REST sees two live SELLs both
before and after the window; ordinary refresh cancellations use cancelSource1.
This is sampled evidence, not uninterrupted or two-sided quote qualification.

At14:44:00 UTC, seven new real Spot Demo fills after Start match quantity, price
and original fees exactly, with no duplicate API identities. Native perpetual
quantities still match LONG0.0030 / SHORT0.0048 BTC. At14:44:38 UTC, all three health
endpoints return200, no HALT, and two Spot SELLs are working. Retrieved logs through
14:44:53 UTC have no application ERROR or scheduled restart. Startup has one
300.734ms probe timeout and a6.039s controller progress warning before the measured
window; subsequent no-quote warnings are perpetual inventory-limit diagnostics.

The bot is left trading. Spot BUY remains blocked by inventory skew; perpetual
quotes remain blocked by the existing exposure cap. No guard was relaxed. The
local rehearsal DB and temporary local SQL proxy were removed/stopped. The backup
and private receipts/proofs remain available. Full release/long soak and the prior
thin-book/probe investigations are separate scopes.
