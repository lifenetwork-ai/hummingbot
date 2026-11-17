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
