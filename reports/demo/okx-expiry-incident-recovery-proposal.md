# Demo expiry incident recovery proposal — operator review

The 2026-11-03 Random Demo test stopped with one unresolved spot submission and
a durable HALT. The stopped bot container has no live owner, and a private
post-stop backup is retained. Authenticated, read-only exchange checks found no
open orders, one 51603 response for the affected ID, no matching fill in the
recent untruncated page, and no matching terminal order in the bounded history
since just before creation. An independent review matched 356 other orders to
terminal canceled orders; another ID also returned 51603 but is already closed in
DB. Current spot BTC cash and perpetual positions match, no new fills were found,
and pre-existing fill rows are unchanged. These observations and the logged
expiry message support review but the original 50036 payload itself was not
retained; absence in exchange responses is not proof of every possible history.

Proposed local Demo-only transaction, **not yet applied**:

1. With the bot stopped, lock the existing advisory ownership key and verify
   stopped/acknowledged/no-owner/HALT state, one pending `OrderFailure` spot
   intent without an exchange ID, and exact fingerprints of all other orders,
   trade fills, positions, order statuses, market tracking state and recovery state.
2. Set `creation_pending=false` only for that intent and remove only its saved
   in-flight tracking entry. Preserve the order itself and every fill/position.
3. Clear this incident's `halt`, `runtime_active`, `shutdown_incomplete` and
   reason. Other recovery fields remain intact. Fresh venue checks, another
   backup, and the same exact DB preconditions are required immediately before
   application. Abort if anything changed; never automatically retry.
4. Start only a newly tested Demo image, perform a stopped read-only preflight,
   then resume the previously requested bounded Demo checks with unchanged risk
   guards. Stop and review if new evidence conflicts.

A clone restored from the stopped backup passed this exact SQL once, retained
fingerprints for all other orders, fills, positions and order statuses, and
rejected replay. The clone contained 11 existing fills and three positions after
repair; the canonical database has not been modified. Reviewed SQL and the dry
run's SHA-256 are in `build/checkpoint-poll-release-20261103/` as
`reviewed-expiry-incident.sql` and `expiry-incident-dryrun.json`. SQL hash:
`471c61187bbb0e8a9be5425024bb651c0453e7e4e02490b2da33169ee85dfb9d`.

The required review comes from [PostgreSQL operations](../../docs/postgres-runtime.md),
which requires an explicit operator-reviewed database maintenance change after
reconciliation; the prior approval applied only to the earlier reconstruction.
The missing executor/session-event history from that earlier backup remains a
known audit limitation.
