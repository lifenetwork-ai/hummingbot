# Demo Random rollover: unresolved submission

On 2026-11-03 image `e9845e3f` passed three spot fixed-profile quote checks and
a Random active-session restart. At the configured step boundary, OKX returned
an expiry error message for one spot create; shutdown's order-status read then
returned 51603. The engine repeatedly restarted but startup reconciliation
blocked the unresolved intent. Random did not advance and the Demo acceptance
failed. This is a real recovery/availability failure observed during Demo,
not a completed acceptance or evidence of a Random selection calculation defect.

Cleanup stopped trading and observed zero open exchange orders. The DB retained
one `creation_pending` order without exchange ID and a durable HALT; the owner is
available and desired state is stopped. A private post-test DB backup was taken.
Authenticated read-only checks after the incident found OKX order-details 51603,
no matching record in the recent fills page (fewer than 100 entries), no match
in the bounded order-history query from five seconds before local creation, and
no open orders. These checks do not establish a complete historical account
reconciliation. The original submission log contains the expiry message but
not its structured response or correlated client ID fields, so the new source
fix cannot retroactively prove this specific intent was rejected.

Source commit `0a619a57f` recognizes only a correlated OKX 50036 response with
the same client ID and empty exchange ID. It resolves that request only after a
fenced durable write; mismatches, uncertain responses and failed persistence
remain halted. Native regression, 319 PostgreSQL tests, Swagger and connector
and shutdown regressions pass. The response shape from this actual incident and
post-fix Demo behavior remain unverified. The earlier independent local R1/R2
load acceptance belongs to image `e9845e3f`, not the changed source.

Do not automatically clear HALT or alter the historical intent. A specific,
operator-reviewed maintenance action requires complete account/order/fill and
position reconciliation first, under [the recovery procedure](../../docs/postgres-runtime.md).
The previously approved backup reconstruction does not authorize this later
incident's maintenance. The original Demo archive is incomplete for executor
and session-event history between backup and reconstruction.

## Subsequent recovery

The operator separately approved the exact intent repair and stop-checkpoint
reset. Both were applied with fresh readback, backup and DB guards. Current
status and remaining REST trade-level proof are in the
[checkpoint recovery report](okx-checkpoint-recovery.md); the stopped/HALT state
described above is the historical incident state.
