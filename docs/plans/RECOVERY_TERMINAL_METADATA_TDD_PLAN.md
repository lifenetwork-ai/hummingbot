# Verified terminal-order metadata in admin recovery

User decision, 2026-11-17: keep Demo stopped. Do not apply the supplemental
five-order database maintenance proposal. Implement the recovery API path first.

## Contract and scope

Keep the existing admin-only Check → review → Reset flow. A matching reviewed
financial checkpoint may include at most64 locally acknowledged maker orders
whose immutable submission identity/account/credentials and zero-fill tracker
are complete. Every unsettled attempt and nonterminal tracker must be covered.
Unknown/prepared/pending sends, fills, missing identity, unsupported contexts or
financial/binding/journal drift retain their existing recovery blockers.

Check performs GET-only, positive identity/terms/cancellation and zero-fill
verification for every candidate, together with the unchanged complete two-pass
checkpoint suffix/account exposure proof. Expose typed `proposed_order_changes`
separately from position repairs; no metadata, financial or HALT changes in Check.
Not-found, an empty pending list or elapsed time alone never proves a cancellation.

Reset independently repeats all venue proofs and compares the exact reviewed
metadata plan. Under the existing final owner/control/actor/ledger/risk fences,
apply only OrderCancelled metadata, terminal attempt state, exact tracker removal,
native-backed cancel history and per-order audit in the HALT-clear transaction.
Preserve submission receipts/context, all fills/positions, financial checkpoint
costs, credentials, risk policy and peaks. Any drift rolls back the entire batch.
The response exposes `repaired_orders`; success leaves trading stopped.

No schema, env or risk change. No connected Reset/Start or new deployment is
implied by local implementation. The five current Demo orders remain untouched.

## TDD checklist

- [x] RED: large anchored ledger with acknowledged zero-fill tracker work can
  retain a bounded candidate snapshot without using the lifetime repair caps.
- [x] Check returns order previews while leaving every ledger row/HALT intact.
- [x] Fresh confirmed Reset atomically completes only reviewed metadata,
  keeps financial/risk rows unchanged and remains stopped.
- [x] Reject pending/uncertain sends, fills, malformed/missing/duplicate trackers,
  unsupported receipt/context, binding/credential drift and over64 candidates.
- [x] Reject not-found, nonterminal state, changed order terms/hedge leg, nonempty
  fills, malformed/missing evidence and local/venue drift between Check/Reset.
- [x] Verify checkpoint two-scan progress, bounded reads, full account open-order
  and exposure checks, canceled worker and owner/control/actor races remain fenced.
- [x] Verify metadata batch rollback and immutable receipt/context preservation.
- [x] Update API/OpenAPI, generated client types, operator and FE docs.
- [x] Run focused PostgreSQL/native recovery regressions and relevant hooks.
- [ ] Connected acceptance after an explicitly deployed image; stays open locally.

## Local qualification

Three RED observations are retained: the large acknowledged-order ledger falls
back to the lifetime cap; an unreviewed metadata Reset is accepted; and adding a
default field changes the original legacy retry body. The implementation keeps
the existing full checkpoint proof and adds positive status/fills/status evidence,
then rechecks account heads/open orders/exposure after metadata reads. Reset
requires `confirm_order_changes=true` for nonempty previews. Request serialization
preserves omitted fields so historical idempotency identities remain unchanged.

PostgreSQL regression104 passes before the final account-recheck addition. Final
scope30 passes, including three real supervisor/API/engine lifecycle cases with
local KMS and blocked external network. The mixed five-order case includes
three Spot and two Perpetual cancellations, both hedge legs, three unchanged
position rows and nonempty journaled fills. Native140, seven recovery-hook tests,
TypeScript, schema/type drift checks, ESLint and hooks pass. Scopes overlap.
The current dashboard intentionally sends false: it cannot authorize metadata
repairs until it displays and explicitly reviews the new order list. The new
API/Swagger contract can carry that confirmation; no automatic retry promotes it.

A READ ONLY connected prerequisite check with the local candidate validator
recognizes exactly the five current Demo orders. It confirms stopped control96
and retained HALT; it is not connected execution of the new API. The SQL proxy
and disposable test containers are closed. No extra metadata repair, deployment,
Reset or Start occurs in this task. Evidence: `build/recovery-terminal-api-20261117`.
