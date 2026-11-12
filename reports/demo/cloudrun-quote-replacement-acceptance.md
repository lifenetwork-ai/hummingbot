# Cloud Run quote/recovery acceptance — 2026-11-12

Revision111 (`hummingbot-demo-00111-s7c`) runs commit `d8f90c13b`, image
`sha256:b1a94a1934773b8aef811b2bc905bba1ec384d962af969a3b5bfc065f1820870`,
with100% traffic,2CPU/1GiB, CPU throttling disabled and min/max1 instance.
The old50037 startup block is resolved. The bot is left running continuously
on control84 and the existing session, without a manual Start, HALT reset,
credential change, risk change or Cloud database maintenance in this follow-up.

The30-minute quote target **fails**. A separate, confirmed post-only CLOSE
retry defect has a tested local fix; that fix is **not deployed on revision111**.
These are separate outcomes, not a full release or production acceptance.

## Confirmed automatic recovery and financial state

- Owner484 retires the exact old50037 intent under the normal native recovery
  path. The order is `OrderFailure`, has no venue order ID or fill, and is absent
  from saved tracking. Exactly one runtime audit records
  `automatic_recovery_order_rejected / exchange_expired_request`. OKX still
  returns51603 for that ID; the positive saved rejection receipt is the authority.
- Two complete authenticated native scans verify2,456 recorded fills:1,304 Spot
  and1,152 perpetual. Account bindings, IDs, prices, quantities and original fees
  match. At the stable exposure read, the native/held perpetual SHORT is0.0392BTC,
  LONG is zero, and no unapplied fills remain. All observed pending orders have
  matching native identity/quantity/price and are post-only. This proof is scoped
  to the fixed cohort at06:26UTC; later fills are not silently included.
- Replay from the previously verified1,805-fill stopped baseline retains every
  original fill unchanged. All651 subsequent fills and643 native applied-order
  receipts reproduce financial rows and remaining principal/opening fees exactly.
  There are zero unsettled fills in that snapshot. The replay includes191 close
  fills, including47 post-deployment SHORT close fills; each close covers its
  allocated opening costs/fees and actual closing fee. Funding and account-wide
  mark-to-market PnL are separate from this trading-fee check.

## Regular Random quote window

Measurement:05:52:36–06:22:38UTC,1,801.318s,901 samples, maximum sampling gap2.391s.
One revision/session/physical owner is retained. Parallel status/liquidity reads
have no request failures. Status p99 is273.11ms; liquidity p99 is174.02ms. These
are API request latencies, not engine order-placement intervals.

The original conservative classifier remains unchanged. Unknown is failure;
known guard exclusions are separate and are never counted as working quotes.
Five cross-generation reads are already classified unknown.

| Market | Both sides working | Eligible gaps | Unknown | Guard excluded | Conservative result |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot |557|24|15|305|557/596 =93.46%, fail |
| Perpetual |726|83|17|75|726/826 =87.89%, fail |

The earlier revision110 window was95.32%/92.65%. This window is worse on that
metric. Different market movement, inventory and cancellation traffic prevent
assigning the entire change to the patch without a controlled comparison.
The code/evidence below establishes one concrete contributor; it does not
explain every guard, unknown sample or probe delay.

No incident HALT requiring reset or physical-owner restart occurs. Three raw
status samples expose the temporary shutdown fence around ordinary profile
changes; they clear automatically and remain unknown/failing samples.
The observed phases are839 running,57 waiting for market readiness and5 starting.

The same window retains38 isolated engine-probe timeouts, approximately301ms
against the300ms probe budget. It also has two ERROR entries for one locally
blocked Spot BUY (`spot_outflow_reconciling`), four other blocked-order entries,
two missing-quote warnings and two non-operator outflow incidents. These are not
presented as a clean-log run or proof of uninterrupted subsecond engine ticks.
The exact duration/cause of every slow probe is not established.

## Post-only CLOSE retry defect and local correction

A native BUY closing SHORT is accepted then immediately canceled by OKX with
`cancelSource=31`: its post-only price would take liquidity. Nineteen
submission attempts in a two-minute cohort belong to the same executor and
reuse the prepared price73623.53. This path existed before the wake optimization:
a RUNNING managed order executor clears its canceled order and retries with its
already-prepared configuration instead of returning pricing authority to the
controller. It is not an ambiguous request outcome and does not need a HALT reset.

The local correction retires that managed maker CLOSE on a matching terminal
cancel. Original/partial fills and native references are retained. Native
financial application releases the level before the controller authorizes a
fresh, fee-covered, passive quote. Unmanaged retry behavior remains unchanged.
No cap, fee floor, price guard, quantity reservation or terminal proof is relaxed.

- Four native lifecycle cases fail before the correction and pass afterward,
  covering BUY/SELL with zero/partial fill. Foreign and duplicate events and the
  unmanaged path remain covered.
- Main native regression:3,491 tests plus16 subtests pass. The35 relevant
  PostgreSQL reduction/terminal tests pass; this is not a new full740-test run.
- The prior packaged image fails the new SHORT post-only-cancellation gate,
  even with its longer recovery allowance. Failed evidence is retained.
- The candidate passes packaged LONG and SHORT cases with a20s requote bound,
  one actual cancelSource31 event each, partial-fill application exactly once,
  original fees/PnL, lost-ACK recovery without duplicate POST, cold restart,
  fee outage/recovery and full return to opening quotes.
- Candidate image is `sha256:88baa812f52f625c43476598ef86fb40ebcf2f661d483c9557115a01278dd609`.
  Its installed order-executor source matches the local correction. It remains
  local; a new deployment and connected quote window are still required.

## Timestamp precision remains a separate limitation

Three unique recorder event-clock timestamps differ from REST execution time by
1,001–1,074ms. The retained native trade snapshots preserve the exact execution
timestamps, and economic values match. The earlier one-second sanity assertion
therefore remains a recorded failure; it was not widened to obtain a pass.
The existing reviewed manual-recovery verifier also rejects such deltas when
those fills are in scope. No historical timestamp was rewritten and no recovery
permission was bypassed. Any enhancement must use positive native timestamp
proof, not a larger arbitrary tolerance.

## Evidence and remaining work

Private read-only evidence is under
`build/cloudrun-quote-replacement-20261112-054820Z/`: deployment/recovery summaries,
`window/demo-quote-analysis-v3.json`, native and financial proof, timestamp/native
snapshot comparison, probe/log summaries and the actual repeated-price cohort.
The first observer attempt failed before sampling because its helper omitted an
ORM model import. It is retained separately; only the completed `window/` is used.

The old-image failure is `build/e2e-offline/20261112-132633-15f232/`.
Candidate SHORT/LONG passes are
`build/e2e-offline/20261112-132920-6bcfcd/` and
`build/e2e-offline/20261112-132920-6f1d11/`.
Local logs: `/tmp/hbot-live-cancel-regression.log`,
`/tmp/hbot-managed-live-cancel-postgres.log` and the corresponding E2E logs.

Next: commit/push the local CLOSE-cancel correction, verify the next CI revision,
then repeat the unchanged quote target. Keep the99% gate open. Broader connected
fault injection and real Google/GCP KMS acceptance retain their prior scope.
