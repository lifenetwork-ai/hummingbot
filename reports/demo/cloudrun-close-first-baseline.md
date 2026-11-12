# Demo close-first baseline and connected acceptance — 2026-11-12

Status: verified baseline, actual CLOSE and cold owner handoff completed under
the operator's delegated Demo/release acceptance scope. The regular-profile
thirty-minute quote window is complete and fails the99% target. At the end of that measurement the bot was left
running continuously after a second verified cold handoff. A later owner483
startup is blocked by a saved50037 rejection; see the
[follow-up](../performance/mm-quote-replacement-latency.md). Scope is OKX Demo only.
Revision110 contains activation commit `3af2e3419`; revision109 contained the
locally qualified runtime commit296166681, with sixteen installed source files
checked against qualification. Maintenance initialization audit242 is separate
from ordinary API actions.

The initial maintenance Stop83 was acknowledged before initialization. Start84
now runs a new Random Spot + Perpetual session continuously, without an expiry.
The current policy preserves0.05BTC per leg,0.1BTC gross and1000/2000USDT drawdown.

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

## Connected acceptance

- [x] Validate the authenticated native fee response before activating the policy.
- [x] Apply and audit the freshly revalidated sidecar; restore automatic service
  scaling with the same image/resources/env and verify cold preservation.
- [x] Deploy only the explicit Demo YAML policy (`close_first` / `breakeven`). Keep
  0.05BTC per leg,0.1BTC gross and1000/2000USDT drawdown unchanged. The general
  catalogue default and Spot policy remain disabled.
- [x] Start a new continuous session, verify native maker CLOSE wire terms on both
  hedge legs and reconcile any natural fills, original fees and remaining costs.
  Unfavorable prices do not authorize a loss-taking or taker fallback.
- [x] Verify cold restart preservation after actual CLOSE fills.
- [ ] Meet the regular thirty-minute eligible two-sided objective.
  Unknown counts as failure; an empty eligible denominator
  is not a pass. Record unfilled/blocked sides without forcing fills or guards.

The guarded transaction changed exactly one checkpoint sidecar and inserted one
maintenance audit. No financial position or risk JSON changed. Automatic scaling
was restored to min1/max1 with the original image, resources, env and traffic.
The authenticated native fee reader accepts the real Demo maker rate0.0002;
runtime reads remain independently refreshed and expire normally.
Cold verification on revision109 confirms both initialized bases survived,
control83 remained stopped/acknowledged and no HALT was present before rollout.

Revision110 is deployed with the expected catalogue hash
`fcbfa06d964a7cfee9439cdc73c70d10ed24ac77245f254aefbf44025fec5f2a`.
Start84 created session `5cb18a14-1a35-4388-b2e1-a49b6acc501d`.
GET-only native checks confirm BUY/short and SELL/long CLOSE orders, `post_only`,
contract/base conversion, exact committed intent terms and fee-covered prices.
Reservations stay below both committed owned quantities and fresh venue legs.

Before cold handoff, nine native LONG CLOSE fills consumed0.0027BTC for
200.902116USDT with0.0401804232USDT original closing fees. LONG fell from0.0500
to0.0473BTC; SHORT remained0.0498BTC. Two complete native scans verify all1,696
fills (910 Spot,786 perpetual). Perpetual lifetime financial replay and remaining
cost replay are exact. Spot's transition from the accepted initial aggregate is
exact; this does not claim a new full lifetime Spot reconstruction. A natural
SHORT CLOSE fill remains unobserved; its fee-covered BUY quote is verified and
is not moved across the selected breakeven boundary to force a fill.

The controlled handoff temporarily scales the service to zero, retaining the
same revision/template/traffic and desired continuous session. All intents end
terminal and all financial work is applied before the quiescent snapshot.
The first polling helper rejected the transient shutdown halt bit too early;
its failed execution is retained. The native shutdown code fences submissions
with `shutdown_pending` until cleanup completes. The subsequent quiescent
snapshot contains no HALT, pending intent or unapplied journal. No manual reset
or data repair was performed. Restoring automatic min1/max1 produces physical
owner generation480 after479, with identical financial/binding hashes and both
remaining-cost bases. Session/control84 survive; the bot resumes quoting.

The first steady quote window started2026-11-11T19:49:35Z after restart
verification. Its helper stopped at19:58:50Z during the Random profile change,
after277 retained observations (Spot276 two-sided/one allocation exclusion;
perpetual268 two-sided/nine ordinary gaps). That incomplete run remains failed.
The rejected status was not saved by that first helper; the subsequent read
shows the same physical owner/session/control, running without HALT, and no
manual reset. No engine ERROR record was found around the transition. This is
not evidence of a durable protected incident or a completed quote window.

The observer now saves the raw status before validation and distinguishes a
documented `shutdown_pending` fence from a persistent protected incident using
a read-only native recovery-state check. The stricter classifier adds only one
boundary to v2: any raw HALT/error observation is unknown, including an expected
shutdown fence. Such samples still count as failures in the conservative99%
denominator. Four focused observer checks reproduce the old raw-HALT omission,
verify the new unknown behavior and preserve every classification in the277
retained observations, including all ordinary gaps and guard exclusions.
The new full-length window is separate; it does not append the failed run.

The new observer captures the next profile transition at20:08:50Z directly:
raw recovery reports `halted=true`, native state has `halt_kind=shutdown_pending`
with no reason, and the API operator state is `stopping`/`next_action=wait` with
reset/check disabled. Three samples are unknown and count against the target.
The same physical owner480 resumes generation3 without operator recovery. This
proves the temporary-fence pattern; it does not recover the first helper's
discarded status or turn the incomplete first window into a pass.

## Completed regular Random window

The separate window runs20:01:43–20:31:45 UTC on November11 (03:01–03:31 local
November12). It retains898 samples over1801.915 seconds, one revision/session
and physical owner480. Maximum sample gap is8.596 seconds; observation quality
passes. The99% quote objective fails on both markets:

| Market | Two-sided | Ordinary gap | Unknown | Guard-excluded | Eligible + unknown | Conservative quote fraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Spot | 489 | 16 | 8 | 385 | 513 | 95.3216% |
| Perpetual | 819 | 57 | 8 | 14 | 884 | 92.6471% |

The eligible-only fractions, which omit unknown, are96.8317%/93.4932%; they
also fail99%. Guard exclusions are not uptime. Seven unknown observations occur
around three Random profile transitions; one has a status observation6.55s old
after sequential API reads, while the subsequent liquidity observation is fresh.
No exact engine stall duration is inferred from that request gap. Twelve probe
timeouts and five transient `spot_outflow_terminal_unavailable` warnings occur
in the window, with zero ERROR-or-higher records and no engine replacement.
These observations do not establish zero quote downtime or a unique latency cause.

A read-only sample of the latest20 fully filled perpetual orders with an
acknowledged same-level/same-generation replacement measures native last-fill
time to the next native order's `cTime`: minimum0.538s,median1.1915s,maximum5.508s.
This is venue creation timing, not local ACK processing or continuous quote
downtime. Instrumenting fill→terminal/financial application→next proposal→send
is the next optimization boundary; no guard, threshold or pricing policy is
changed to make the target pass.

## Final financial proof and return to continuous running

After measurement, a controlled quiescent snapshot has14,084 orders,1,805 fills,
three positions, zero unresolved attempts and zero unapplied journal work.
Two full native scans match945 Spot and860 perpetual fills and their original
fees, with both account bindings and venue hedge quantities exact. A separate
live-read attempt earlier saw additional native fills after its snapshot; that
failed attempt is retained and is not called a database discrepancy or a pass.

Since initialization,119 fills map to118 immutable native applied-order receipts.
Replaying those receipts in application order from the accepted initial
aggregate matches all three native financial rows and both remaining-cost bases
exactly. Original1,686 fills are unchanged. This does not infer a new lifetime
Spot baseline or invent application order from averaged prices.

There are82 LONG CLOSE orders with83 native maker fills (`execType=M`), totaling
0.0246BTC for1832.529441USDT and0.3665058882USDT closing fees. Every close covers
its allocated original opening fees and actual closing fees. The summed excess
after those trading fees is21.80506313724USDT, excluding funding and other
account/strategy PnL. No new perpetual OPEN fill is observed. LONG is0.0254BTC;
SHORT remains0.0498BTC. A natural SHORT close fill remains unqualified; its native
fee-covered BUY quote is verified without forcing a fill.

Automatic min1/max1 is restored with the same revision/image/template/resources
(2CPU/1GiB). Physical owner481 replaces480, preserving session/control84 and
the exact financial/binding hashes and remaining-cost sidecar before new
activity. The bot resumes with both markets quoting, no HALT, and no expiry.
Neither handoff uses manual HALT reset, financial repair or relaxed risk limits.

The local activation configuration passes53 existing configuration/deployment
tests. No new engine code is introduced by this activation change.
