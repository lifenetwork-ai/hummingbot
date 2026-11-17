# Demo recorder ACK recovery — 2026-11-17

Status: initial one-intent maintenance applied with operator approval, audit357.
The operator declines the supplemental five-order maintenance and selects:
keep Demo stopped; implement the normal API repair flow first. Do not apply
the prepared five-order maintenance script. Stop succeeds
through the admin API at control/ack96. Recovery Check
`1c2cb4cb-d807-4da5-a094-c1aeda4f9b54` fails with
`recovery_pending_intents`. HALT remains; no Reset or Start is submitted.

## Cause

Revision166 persists `trading_health_failed` at14:31:30.806152UTC. Processing
the Spot create ACK encounters PostgreSQL55P03: the recorder's ownership
`FOR SHARE` on BotState exceeds its1s lock timeout. Its financial transaction
rolls back; persistence health latches false, cleanup fails, and the engine
restarts with the durable HALT. The exact historical blocker was not captured.

Order `93027a12dac34fBCBBCUTff1dd50a2b2` is left PendingBuy/creation_pending,
with no recorded venue ID and no local fill. Its immutable submission attempt
is `possibly_sent` but retains a successful HTTP200/code0 receipt identifying
venue order `4107850619231293440`. This corroborates the lost recorder ACK;
it is not a venue rejection or a drawdown stop.

Revision167 deploys exact `d87cccdf6`, which removes demonstrated long exclusive
BotState locks from speculative publication and funding work. Eight original
RED cases reproduce those contention mechanisms. Final PostgreSQL100/native57
and hooks pass. This fixes known causes without weakening financial fences;
it does not identify the historic blocking transaction or promise zero future
database/ownership failures. The five-minute HALTed deployment observation is
not a trading-load qualification.

## Positive venue proof and precise maintenance scope

Two authenticated GET-only native checks verify the currently bound Demo
account. Both find the exact Spot BTC-USDT post-only BUY, price64802 and
quantity0.0003639BTC, venue state canceled, cumulative fills0, fee0. The venue
ID matches the original success receipt. Both find zero pending Spot orders
and zero native fills for this order. The single saved tracker entry has no
executed quantity or fills. All connected DB inspection is read-only.

Proposed operation, requiring the separate operator review documented in
[PostgreSQL runtime operations](../../docs/postgres-runtime.md):

1. Recheck native evidence, stopped control96, revision167/image and account.
   Temporarily drain the Demo writer using service-level scaling0; wait for the
   physical advisory owner lock to release. Preserve and restore scaling.
2. Acquire the existing ownership advisory lock. Require exact unchanged order,
   attempt receipt/context, pending identity, tracker and zero local fills.
3. Atomically complete only that order's metadata: venue ID from the receipt,
   creation_pending=false, last_status=OrderCancelled and native cancel time.
   Mark its attempt terminal, preserving the original receipt/context/deadline.
4. Remove exactly that stale Spot tracker entry, add two native-backed history
   rows (BuyOrderCreated and OrderCancelled) and one maintenance audit. Keep
   HALT untouched. Preserve all56,473 orders,15,250 fills,3 positions and all
   financial/risk/credential/checkpoint tables; compare table hashes in the
   drained transaction. No fabricated OrderFailure or absence inference.
5. Restore the original service scaling with image/env/resources unchanged.
   A fresh normal API Check must prove the full exchange suffix; review its
   proposed changes before Reset. Only after successful Reset may the same
   both/fixed/follow_market/until_stopped session intent Start separately.
   Observe live trading beyond the prior31-minute failure point.

The identical row-repair function passes on disposable PostgreSQL14 containing
the incident rows and canonical schema. Four negative cases reject and roll
back: venue-ID drift, amount drift, positive venue fill and repeated apply.
No production metadata or HALT was modified by this rehearsal. Earlier
local startup/read-helper failures made no production writes and did not count
as passing rehearsals. The disposable container is removed.

Private evidence: `build/recovery-background-write-20261117`, including Stop and
failed Check receipts, exact pending rows, two native proofs, rehearsal result
and a prepared maintenance script. Secrets are excluded from this report.

## Availability implications

The immediate prevention is shortening background exclusive-lock sections,
already deployed. Once a recorder transaction has rolled back after a real
venue ACK, ignoring persistence failure would permit local/exchange divergence.
The remaining operational gap is stopped recovery for positively identified
terminal intents: the current API blocks all pending intents before loading
read-only exchange readers, so it cannot yet preview/confirm this zero-fill
repair. Supporting it requires a bounded, audited review/confirm path with
fresh identity/terms/fills/account proof, owner/control revalidation and atomic
tracker/attempt/history updates. Not-found or elapsed time must remain
inconclusive; filled orders need full journal/position reconciliation. No
automatic or manual blanket HALT reset is proposed.

## Initial approval applied; remaining terminal batch for review

The operator explicitly approves the one-intent proposal in the conversation.
Fresh native evidence repeats before maintenance. The initial client-side
whole-table checksum collector is canceled before the mutation marker or any
row repair; the transaction rolls back and original scaling is restored.
Checksums are then computed on the server, avoiding bulk row transfer.

The completed transaction acquires the released physical ownership lock,
requires stopped control/ack96 and commits audit357. Exactly five tables change:
one order, one attempt, one tracker row, two new order-status rows and one audit.
All23 other table checksums match, including all15,250 fills,3 positions, complete
credentials, risk, runtime checkpoints and journal. Order count stays56,473.
HALT is unchanged. Original scaling/template is restored and revision167 owns
the fresh stopped state.

Fresh API Check `2e62c349-42ef-4516-9cd5-ca9d65d11606` now fails with
`recovery_scope_limit`. Read-only diagnosis identifies why verified-checkpoint
mode is ineligible: five other acknowledged attempts and five nonterminal
saved trackers remain from the same failed cleanup. Financial hash, binding
hash, environment and reviewed anchor match; pending orders and unapplied
journal rows are0, and the historical unjournaled prefix remains650 exactly.
No financial or anchor repair is indicated. The fallback's small lifetime
history window then produces the generic scope-limit error.

The complete remaining set is:

| Client order suffix | Market | Venue ID | Intended side |
| --- | --- | --- | --- |
| `BCBBCUT24bbd3feb22` | Spot | `4107850615875850240` | BUY |
| `BCSBCUT1a24d7baee5` | Perpetual | `4107849048883875840` | SELL |
| `BCSBCUTa513435ba9a` | Perpetual | `4107849010162061312` | SELL |
| `BCSBCUTb022ed649c0` | Spot | `4107850494710796288` | SELL |
| `BCSBCUTb2f934359b2` | Spot | `4107850495918755840` | SELL |

All client IDs have the unchanged prefix `93027a12dac34f` before those suffixes.
Two fresh GET-only native passes match every order's account, ID, symbol, side,
post-only type, price and base quantity. Every order is canceled with0 cumulative
fills; every exact-order fill query and both complete account pending lists
are empty. Perpetual quantities use native contract conversion and their hedge
legs are checked against original immutable intent context. No absence inference
or resubmission is involved.

**Supplemental operation requiring operator review:** drain the same Demo writer,
acquire its physical advisory fence, and in one transaction mark these five
orders OrderCancelled at their native terminal times, mark their five original
attempts terminal without changing receipts/context, remove exactly five tracker
entries across two market rows, add five cancel-status rows and one batch audit.
Keep HALT, all counts of financial rows, risk, credentials and checkpoints
unchanged; enforce the same table checksums and original scaling restoration.
Then repeat normal fresh Check/Recover and separate Start. Approval of the
initial single-intent repair does not authorize this extra batch.

The shared batch function passes on a disposable canonical PostgreSQL14 clone;
venue-ID drift, quantity drift, a positive fill and repeated application all
reject atomically. The read-only connected preflight also passes. The five-order
batch has not been applied and is superseded by the API implementation decision.
No Reset/Start has been sent. Evidence:
`remaining-reference-private.json`, `remaining-native-proof-private.json`,
`remaining-rehearsal.json`, `remaining-maintenance-preflight.txt` under the same
private evidence directory.

## API path implemented locally; current Demo remains stopped

The follow-up implements Check/review/Reset for bounded acknowledged maker
orders with complete identity and zero local execution. Check combines the
complete checkpoint suffix proof with positive native cancellation/fill checks
and returns typed `proposed_order_changes`. Reset independently repeats proof,
requires explicit `confirm_order_changes=true` and applies only the identical
reviewed metadata batch atomically with per-order audits/HALT recovery. Recovery
success still leaves trading stopped. Unknown sends or execution remain blocked.

The local validator, in a connected READ ONLY transaction, recognizes exactly
these five orders at control96/stopped/HALT. No extra connected metadata is
changed. Final PostgreSQL30/native140, seven affected FE-hook tests and relevant
checks pass; the earlier PostgreSQL104 regression is an overlapping scope.
The API artifact is not deployed or connected-qualified. The current dashboard
does not yet render/review this order list and deliberately sends false; use
the new API/Swagger confirmation contract after explicit review. See
[implementation scope and remaining gate](../../docs/plans/RECOVERY_TERMINAL_METADATA_TDD_PLAN.md).

## Revision168 connected attempt: old native head outside recent fills

On2026-11-18 local time, CI deploys `3b9d40bff` as
`hummingbot-demo-00168-4lc`, Ready/100% traffic. The new OpenAPI fields are
present; owner/control remain fresh and stopped96. Check
`47404c4e-59a1-4f67-a342-56cc307e9457` checks81 Spot fills, then blocks with
`recovery_exchange_scope_incomplete` before any metadata/HALT repair.

The saved Perpetual head is a2026-11-12 fill. The anchor's2026-11-17 review
date makes the old selector use the three-day fills endpoint. Direct GET-only
comparison returns0 recent Perpetual fills, while archived history positively
contains the exact saved head. This is an endpoint-selection bug, not proof
that the five metadata orders are missing or filled. The local TDD fix selects
archive history for ID anchors in both verifier paths;179 regression tests pass.
The fixed verifier also passes a GET-only/READ ONLY run against actual Demo:
both81-fill scans, all five positive cancellation/zero-fill proofs and the final
account-head/open-order/exposure recheck. Proof progress stays in memory; this
does not mutate the ledger or replace connected API qualification. Full row/hash
comparison confirms the failed deployed Check leaves the five orders/attempts/
trackers, financial checkpoint, fills/positions and risk unchanged.
The connected Reset gate remains open until that fix is deployed and a fresh
Check/review succeeds. No Reset, Start or supplemental SQL maintenance is sent.

## Revision169: five-order API recovery completed

CI deploys the aged-head fix `8036235c2` as `hummingbot-demo-00169-cj5`,
Ready/100% traffic; the resolved image digest is
`sha256:b8d9705ab486efc36bfe77dd87181237f901c11dd138f8f5d20a3965cf7544f2`.
Recovery waits for the new physical owner after API readiness. Fresh Check
`f42415c3-d559-47ed-99e1-33193cb8675e` verifies both81-fill suffix scans
and exactly the five canceled zero-fill orders listed above, with no financial
repair or blocker. The reviewed Reset
`f8c19469-e4a5-449e-bd76-c23c8b8ad86a` independently revalidates, atomically
repairs those metadata rows and clears the retained technical HALT.

The before/after READ ONLY comparison passes: five exact OrderCancelled updates,
five terminal attempts, five tracker removals and five cancel-status inserts;
original receipts/contexts and unrelated rows match. Orders56,473, fills15,250
and positions3 stay unchanged; statuses183,308→183,313. Runtime journal,
credentials, financial checkpoint costs/payload/watermark, risk and pinned
catalogue match; its reviewed recovery anchor alone advances to this Reset at
journal watermark89267. The same owner/generation completes the operation.
Five per-order success audits carry the actual Basic admin identity and Reset
ID, as confirmed through `/audit?operation_id=...`.

Final requested state is stopped, control/ack97, no HALT. No Start or extra
manual SQL repair is performed; the rejected supplemental maintenance remains
unused. This is connected API recovery acceptance, not running-trading/soak or
dashboard metadata-review acceptance. Evidence:
`build/recovery-aged-head-postdeploy-20261118`.

Post-recovery observation: seven fresh samples over182.3s keep the same169
owner, stopped control97/no HALT, startup/live200 and expected stopped ready503.
The bounded application-log query returns two recovered loop stalls about269/
271ms and a217.5ms DB ping diagnostic, without application ERROR/restart/new HALT.
One admin-access denial comes from the helper's incorrect audit URL, corrected
to the documented `/audit` route; the actual five repair audits then verify.
No zero-latency, running liquidity or long-soak claim is made. The SQL proxy is
closed; no local test image/container is created during this connected task.

## Authorized continuous Start:30-minute runtime observation

The operator subsequently requests Start and monitoring. The API accepts Start
`9d99ec03-811a-487a-acc9-7455b8c508aa`, then succeeds at control/ack98 on the
same revision169 image. Session `325bfcf3-6651-4aa4-a8df-f0daccb783c4` runs
both markets, fixed follow_market, until_stopped with expires_atnull. The
catalogue hash remains `2e4c399583fa8e8c201b07f2ac9a26f81b987e9ccd44a03011fa463bcd94ba71`.
Credentials stay connected; caps0.05/0.05/gross0.1BTC, close_first/breakeven,
drawdown2500/5000 and all other catalogue values are unchanged. Normal API audit
carries the configured Basic admin identity. No manual SQL repair or risk reset
is used.

The observed window is2026-11-17 17:52:23.849–18:22:25.752UTC,1800.9s on one
revision/session/control. It contains56 HTTP samples; live/ready and fresh runtime
status remain healthy in those samples, with no HALT/restart/application ERROR
returned by the bounded log query. Owner UUID/generation564 matches before/after.
Final READ ONLY counts are56,640 orders/15,289 fills/3 positions:167 additional
order records and39 additional fills since Start, on38 distinct filled orders.
All39 observed fills are Spot,35BUY and4SELL. Ledger growth matches the observed
fill set; zero journal rows remain unapplied. Additional order records include
local guard failures and must not be reported as167 venue-accepted orders.

Native GET-only account reads at the beginning and end confirm two live
BTC-USDT-SWAP SELL orders, posSidelong, reduceOnlytrue, price72,938.67. Perpetual
LONG remains0.0498BTC; there is no Perpetual fill, so actual closing-fill/handoff
acceptance remains open. The fee-inclusive breakeven policy deliberately does
not force a loss-making close. Spot inventory skew initially suppresses SELL
below50% BTC valuation, later admits SELL; multiplier-zero/minimum-size outcomes
are reported as order_size_rejected. Perpetual BUY remains inventory-limited.

**Retained failures/limits:** at1205.5s the public startup endpoint returns503
with stateengine_unavailable, while live/ready return200 and runtime is still
running/fresh/no HALT. An immediate later read returns startup200. The original
strict monitor fails and keeps its receipt. Observation then continues to the
original30-minute deadline without resetting the clock or claiming a new clean
window. The largest HTTP sampling gap during diagnosis is143.26s; this is sampled
evidence, not a continuous uptime proof. The API's engine-health proxy has a300ms
deadline; a391.6ms loop stall at the failed sample includes checkpoint-copy stack
evidence. This supports a timeout explanation, not proof of a unique root cause.

The Start-through-final log query records five approximately301ms probe timeouts
(one during startup, four inside the measured window),31 loop stalls and31
recoveries; maximum recorded heartbeat gap is1286.9ms. Seventeen stall captures
include copy_liquidity_checkpoint frames (nine in publication preparation, eight
in capture). Slow diagnostic spans may include awaits and are not synonymous with
loop-block duration. Two spread-policy order rejections are WARNINGs; no financial
write failure, child restart or new technical HALT is observed. Keep the strict
all-health/latency gate failed rather than widening timeouts or suppressing data.

Server rolling coverage at the final sample retains the full1800s window. Spot
has234.285 eligible seconds,229.133 two-sided eligible seconds (97.801% conditional,
12.784% conservative),1558.006 eligibility-unknown seconds and7.709 policy-restricted
seconds. It does not pass the99% target. Perpetual has no eligible time and about
1799.435 inventory-restricted seconds, so its two-sided target is not qualified.
Both have about0.565s observation-unknown time. Do not reinterpret the remaining
eligibility-unknown Spot periods as measured healthy two-sided quoting.

Bot remains running until stopped; no Stop is sent at the end of measurement.
Follow-ups remain health/loop-latency TDD, two-sided qualification and a real
Perpetual closing-fill handoff. No production/long-soak pass is claimed.
Private evidence: `build/demo-resume-r169-20261118`, including immutable Start,
the failed strict monitor, same-window continuation, native/DB comparisons and
log summaries. No local image/container is created by this observation.
