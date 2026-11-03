# Demo volume reconstruction proposal — operator review

Date: 2026-11-03. No canonical repair has been applied.

The prior local Demo volume and containers have been deleted. The latest private
backup contains1,257 orders/six fills. Current read-only OKX REST/public+private
WebSocket checks pass, with no pending orders and0.0089BTC long/0.0028BTC short.
The backup only contains0.0084long/0.0027short. Five later natural maker fills and
23 terminal orders have been independently re-read from OKX and matched to the
previous accepted Demo test's archived identities/amounts/prices/original fees.

Proposed operation, only after operator approval:

1. Restore the unchanged private backup into a new local Demo volume; keep all
   service processes stopped and retain the original backup.
2. Verify the reviewed stopped/acknowledged/no-HALT/no-mutation state and exact
   six-fill/1,257-order/three-position baseline before any correction.
3. In one transaction, insert the23 proven terminal orders and five actual fills;
   recompute the three position aggregates with the existing PositionHold logic.
   Keep the existing position IDs. Spot changes to a single SELL0.00000409BTC net;
   perpetual becomes0.0089long/0.0028short. Preserve original-currency fees and all
   existing order/fill rows. No account/order mutation, history deletion or HALT reset.
4. Record reconstruction provenance and validate a stopped current-image startup
   before new Demo testing. Stop if fresh venue truth no longer matches this proof.

The complete dry run on a disposable restored clone passes:1,280 orders,11 unique
fills,three positions, unchanged whole-row hashes for the original1,257 orders
and six fills, preserved position IDs, exact aggregate values and no HALT change.
Reviewable SQL and private/raw evidence are under
build/checkpoint-poll-release-20261103/{reviewed-demo-recovery.sql,
recovery-sql-review.json,demo-recovery-proposal.json,demo-recovery-dry-run.json}.
The SQL refuses a changed baseline; it cannot double-apply to an11-fill database.

History limitation: post-backup executor rows and session-event/command receipts
cannot be fully recovered. Reconstructed order-status timestamps use archived
creation and venue terminal proof; this is not a byte-for-byte restoration of
the deleted database. Retain that limitation and the reconstruction note in any
subsequent report. Do not present the recovered history as complete audit history.

This review follows [PostgreSQL operations](../../docs/postgres-runtime.md):
reconcile the exchange with persisted orders/fills/positions before an explicit
operator-reviewed maintenance change. Previous approval for a different historical
repair is not being reused here. Offline load testing continues independently.
