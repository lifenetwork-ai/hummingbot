# Cloud Run quote/recovery acceptance — 2026-11-12

Latest: revision112/`1c1fa280a` has deployed and its connected verification is
complete. The post-only correction is observed natively; the99% quote gate still
fails. See [revision112 results](#revision112-post-only-correction-deployed).
The revision111 record below is historical and retains its original scope.

Earlier revision111 (`hummingbot-demo-00111-s7c`) runs commit `d8f90c13b`, image
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

At that earlier cutoff, the next step was committing/deploying the CLOSE-cancel
correction and repeating the unchanged target; revision112 results follow below. Keep the99% gate open. Broader connected
fault injection and real Google/GCP KMS acceptance retain their prior scope.

## Revision112: post-only correction deployed

`hummingbot-demo-00112-zjc` receives100% traffic. Registry image
`sha256:563a8962b8df3eeb90367941150d15b93d8a5b780102fa18b411ae950f795fa3`
has amd64 config label `commit=1c1fa280a1261ffb1282a6d9fa9a4984cd060344`.
The physical owner485, session and control84 remain unchanged throughout the
new window. No Cloud service/DB/risk/credential change, manual Start, reset or
forced fill was performed. The bot is left running in Demo, `until_stopped`.

### Native correction and money checks

A fixed owner485 cohort contains104 CLOSE submissions from104 executors.
Individual native GETs match all104 orders. Five native `cancelSource=31` cases
are followed by a different executor and a changed price at the same level/session.
Venue-to-venue replacement intervals are762–7,363ms. Three immediate replacements
fill, one is later normally canceled, and one gets another post-only cancel
before the following fresh-price attempt fills. These intervals are not a p99
or a promise that every new attempt immediately rests on the book.
The final owner485 readback has269 managed CLOSE submissions from269 executors;
maximum orders per executor is one. The nineteen-attempt same-executor loop
observed on revision111 is absent in this bounded readback.

Two complete authenticated scans verify2,695 fills at the07:40UTC cutoff:
1,358 Spot and1,337 perpetual. Original fee currencies/amounts, execution IDs,
price/quantity and account bindings match. Stable held/native SHORT is0.0184BTC,
LONG is zero, and there are no unapplied fills at that exposure check. Pending
native post-only orders are Spot2/perpetual4 and match their local intents.

Independent replay keeps all1,805 accepted baseline fills unchanged. The890
subsequent fills and880 applied-order receipts reproduce settled financial rows
and remaining principal/opening fees exactly; the final snapshot has zero
unsettled fills. There are89 owner485 SHORT close fills, each covering allocated
opening principal/fees and actual closing fees. The timestamp-only post-readiness
count is91, including two prior-owner handoff fills; it is not mislabeled as89
new-owner fills. Funding and account-wide mark-to-market remain separate.
The earlier snapshot's one pending fill and the recorder timestamp limitation
above are retained, not rewritten to obtain a pass.

### Unchanged 30-minute quote gate

Window07:10:26.580995–07:40:27.678542UTC:901 samples,1,801.098s,
maximum sample gap2.493s, quality pass. The classifier is unchanged; unknown
counts as failure and guards never count as working quotes.

| Market | Both working | Eligible gap | Unknown | Guard excluded | Conservative result |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot |99|8|7|787|99/114 =86.84%, fail |
| Perpetual |793|35|9|64|793/837 =94.74%, fail |

Spot has only107 confirmed eligible samples; many exclusions are intentional
inventory skew reducing BUY to zero (`order_size_rejected`). One captured account
base share is50.1785%, with BUY multiplier0 and SELL multiplier2. The scope is
account cash, not a claim that both sides are continuously tradable. No inventory
policy was loosened. Perpetual improves from87.89% to94.74%, whereas Spot falls
from93.46% to86.84%. This is not a controlled A/B experiment: inventory, market
movement and observer traffic differ. The supplementary cached `/performance`
GET, once per2s, begins at07:23:32 and does not change the quote classifier.

There are844 running,54 waiting-for-readiness and3 starting observations, with no
status.error or observation request failure. Two raw HALT samples are the
explicit `shutdown_pending` fence during profile changes; each clears in the next
sample. Both remain unknown/failing samples. No incident HALT reset or physical
owner restart occurs. Eligible gap clusters span1–3 samples (brackets up to4.01s
Spot/6.02s perpetual), mostly submission, executor publication and confirmation.
These are sampled brackets, not exact continuous gap durations.

### Probe investigation remains open

The exact window contains45 WARNING entries and no ERROR:35 engine-probe timeout
transitions,3 missing-quote warnings,2 non-operator Spot outflow incidents, and5
locally blocked-order log entries for3 order IDs during readiness recovery.
The earlier07:08:50 ERROR falls outside this window,78.7ms after the stored
`step_changed` event; the blocked order has no DB row. It is retained separately
as a local submission fence around profile shutdown, not presented as a clean
revision-wide ERROR-free run.

Timeout elapsed values are300.789–304.098ms against a300ms supervisor budget.
They are **deadline-censored waits**, not measured engine stall lengths.
The35 versus38 warnings on revision111 do not establish a latency improvement.
Status HTTP p99 is379.80ms, liquidity385.44ms (max871.88/650.37ms); those are
external API observation timings, not local probe or order-placement latency.

The `/live` callback checks in-memory lifecycle/observer progress and contains no
SQL or venue request. Synchronous ownership/recorder work shares its engine loop;
loop scheduling, accumulated DB waits, CPU/GIL bursts and transport scheduling
remain candidate causes. No single blocking frame was captured here, so none is
promoted to the established root cause.

Thirty available one-minute resource points (ending07:11–07:40) show CPU means
5.11–9.74% of the configured allocation and memory means47.47–48.71%. These
coarse points show no sustained saturation; they cannot exclude short bursts.
Recovery/session/status JSON sizes are1,461/5,426/15,522 bytes at the readback;
these payloads do not show an expanding tracker in this snapshot.

The supplementary509 report reads have no HTTP errors. Around18 overlapping
probe timeouts,15 before/after pairs show fresh reports from both controllers
advancing within adjacent2s reads. One other pair is a profile shutdown; two
have unchanged yet fresh cached report timestamps. Those three are inconclusive,
not healthy-tick proofs. There are19 stale controller observations (maximum age
10.13s), including profile changes and a separate perpetual `guard_recovering`
interval. They remain visibly stale. These observations demonstrate scoped MM
report progress, not uninterrupted engine ticks or continuous working quotes.

Read-only sink inspection confirms `drop-cloudrun-below-warning` still excludes
this bot's INFO, including `child_probe_recovered`. Exact recovery durations
cannot be reconstructed from missing INFO events. Logging configuration was not
changed. Keep engine health on its loop; do not move it to an always-responsive
thread or widen the300ms budget solely to remove these warnings.

Remaining work is bounded timing/stack capture on actual slow-probe episodes,
with tick/controller progress and native submission/cancellation/DB stages on
the same clock, followed by TDD for any confirmed blocking path. The quote99%
gate and the previously documented manual-recovery timestamp limitation remain
open. This deployment check does not close full soak or production acceptance.

Evidence: `build/cloudrun-post-only-close-20261112/` contains image/deployment
proof, unchanged-classifier `window/` observations, exact-window logs, resource
points, native/financial proof, CLOSE-executor counts and cached-report/probe
correlation. Failed auxiliary history-reader scope/system-Python attempts are
separate from the successful observer. No recovery-reader permissions were
expanded; native cancellation proof uses permitted individual order GETs.
