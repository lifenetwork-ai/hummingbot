# Demo close-first baseline and connected acceptance — 2026-11-12

Status: applied under the operator's delegated Demo/release acceptance scope;
maintenance audit242. Scope is OKX Demo only. Revision109 contains commit296166681;
the deployed image's sixteen checked source files match local qualification.

The existing bot is intentionally stopped through its audited API at control83.
This is planned maintenance, not HALT recovery. All working submission attempts
are terminal and all financial journal work is applied.

## Verified initialization

The stopped database has13,564 orders,1,686 fills and3 native position rows.
Two independent complete OKX reads match all909 Spot and777 perpetual fills:
native IDs, contract/base units, prices and original fee currencies/amounts.
Both account bindings match and both markets have no pending venue orders.
The native financial replay and an independent rational replay of the opening
tails after verified flat anchors agree with these remaining costs:

| Owned leg | BTC | Remaining opening principal, USDT | Unallocated opening fees, USDT |
| --- | --- | --- | --- |
| LONG | 0.0500 | 3678.853400 | 0.7357706800 |
| SHORT | 0.0498 | 3672.411420 | 0.7344822840 |

These fees are allocated to the remaining opening tail, not copied from lifetime
cumulative fees. LONG has221 opening-tail fills; SHORT has249. The earlier costs
prepared before later trading are not applied.

Expected financial hash:
`a4fdf4cbbe54a6b5622c001b8adbb4ae3343a0ac8d2a005d6bd953c85b1e01f7`.
Expected account-binding hash:
`eea6142a4bc61653c7610eb8bc5be1c21df6ad43ed263ba3b9a53c261ab1c18f`.

Application changes one `runtime_checkpoints.payload.reduction_basis` sidecar
and adds one maintenance audit. It changes no order, fill, position, credential,
drawdown/outflow limit or HALT latch. Stop remains acknowledged; Start is separate.
The maintenance actor is explicitly the delegated agent, not an impersonated
Google login. Fresh venue revalidation, stopped physical writers, the existing
owner advisory lock and matching native financial/binding hashes are required.

A full read-only export was restored in a disposable PostgreSQL16 clone. The
guarded transaction passes; repeat application is idempotent and changed
financial evidence is rejected. Exact position rows and risk JSON remain
unchanged. Private proofs and the rehearsal result are retained under
`build/demo-close-first-acceptance-20261112/`.

## Connected acceptance still required

- Validate the authenticated native fee response before activating the policy.
- Apply and audit the freshly revalidated sidecar; restore automatic service
  scaling with the same image/resources/env and verify cold preservation.
- Deploy only the explicit Demo YAML policy (`close_first` / `breakeven`). Keep
  0.05BTC per leg,0.1BTC gross and1000/2000USDT drawdown unchanged. The general
  catalogue default and Spot policy remain disabled.
- Start a new continuous session, verify native maker CLOSE wire terms on both
  hedge legs and reconcile any natural fills, original fees and remaining costs.
  Unfavorable prices do not authorize a loss-taking or taker fallback.
- Verify restart preservation, then measure the regular thirty-minute eligible
  two-sided objective. Unknown counts as failure; an empty eligible denominator
  is not a pass. Record unfilled/blocked sides without forcing fills or guards.

The guarded transaction changed exactly one checkpoint sidecar and inserted one
maintenance audit. No financial position or risk JSON changed. Automatic scaling
was restored to min1/max1 with the original image, resources, env and traffic.
The authenticated native fee reader accepts the real Demo maker rate0.0002;
runtime reads remain independently refreshed and expire normally.
Cold verification on revision109 confirms both initialized bases survived,
control83 remains stopped/acknowledged and no HALT is present. Activation rollout
and the new-session connected measurements are still pending.

The local activation configuration passes53 existing configuration/deployment
tests. No new engine code is introduced by this activation change.
