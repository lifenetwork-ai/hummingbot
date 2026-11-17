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
- [ ] Connected Check → reviewed Reset acceptance; revision168 Check is blocked
  by the aged native-head history issue described below. No Reset was sent.

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

## Connected follow-up, 2026-11-18

Revision `hummingbot-demo-00168-4lc` is Ready with100% traffic and the exact
`3b9d40bff` image (`sha256:2734fe0cc856349f08cc88f10a09028725ac9ee6df31ca243adc27cc147bf0fe`).
Its OpenAPI exposes the new metadata preview/confirmation fields. The owner is
fresh on that revision, stopped control/ack96 with the original retained HALT.

Check `47404c4e-59a1-4f67-a342-56cc307e9457` verifies the81-fill Spot suffix,
then blocks with `recovery_exchange_scope_incomplete`. Native GET diagnosis
finds the quiet Perpetual anchor absent from `/trade/fills`, but present in
`/trade/fills-history`: its last fill is from2026-11-12, although the anchor was
reviewed on2026-11-17. Choosing the endpoint from the review age incorrectly
assumes its saved native heads are equally recent. The
[OKX endpoint contract](https://www.okx.com/docs-v5/) gives recent fills a
three-day window and archived fills a three-month window.

TDD reproduces the error before the fix in both single-job and segmented proof.
The local fix uses the archive whenever proving a non-null native head, with
the same route through both scans and resumed jobs. Missing anchors, unrecorded
fills, changed heads and existing proof/owner/ledger fences still block. No
schema, env, limits or database maintenance change is needed. Native regression
179 passes, including active Spot plus quiet Perpetual and empty suffix cases;
all affected pre-commit checks pass. A separate local execution of the fixed
verifier against actual Demo data passes both81-fill scans, positive status/
fills/status proof for the five canceled zero-fill orders, and the final account
head/open-order/exposure recheck. Every DB transaction is READ ONLY and progress
is kept only in memory: this does not complete a connected API Reset.
This follow-up is not deployed; the connected gate remains open. Keep Demo
stopped; after deployment run a fresh Check and review the exact five orders
before Reset. Do not reuse the failed operation or start trading.

Evidence: `build/recovery-terminal-postdeploy-20261118` (private native reads,
before/after read-only comparisons, failing Check receipt and RED/GREEN tests).
Read-only hashes/row comparisons confirm the failed Check preserves all five
orders/attempts/trackers,56,473 orders,15,250 fills,3 positions, order-status
count183,308, financial checkpoint and risk. `/startup` and `/live` return200;
`/ready` returns503 for the intentionally stopped/halted bot. Sampled application
logs include the expected recovery blocker and slow-stage diagnostics, including
a1.026s observation and a recovered approximately218ms owner-read loop stall.
These are not successful trading or load acceptance; no new restart or HALT
transition is observed in the sampled revision window.
