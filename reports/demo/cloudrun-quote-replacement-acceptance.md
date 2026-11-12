# Cloud Run quote/recovery acceptance — 2026-11-12

Latest: revision118/`1e5d15bda` completes897 samples on one revision/session.
Observation quality passes, but the unchanged99% target fails: Spot94.50%,
perpetual94.03%. Three profile changes finish without ERROR, restart or shutdown
timeout; transient fences and unavailable data remain failing unknown samples.
A separately reproduced position-poll scheduling defect is fixed and qualified
locally, not deployed. See [revision118 results](#revision118-connected-window-and-position-poll-follow-up).

Earlier: revision117/`5c36bb7bd` has a completed901-sample baseline. Observation
quality passes, but the unchanged99% target fails: Spot98.36%, perpetual97.58%.
No manual reset, ERROR, engine restart or8s shutdown timeout occurs in this
window; temporary shutdown fences remain failing unknown samples. Local proof
sharing, executor-identity and freshness-fixture changes are separately qualified,
not deployed. See [baseline results](#revision117-thirty-minute-baseline-during-local-follow-up).
Revision112's completed verification and the revision111 record below remain
historical, with their original failed99% quote gates and scope limits intact.

## Revision118 connected window and position-poll follow-up

Revision `hummingbot-demo-00118-8f9` is Ready with100% traffic. Cloud Build
`f9ec05e4-e048-4899-943d-c762dd160e30` succeeds for commit
`1e5d15bda8bab72ce6169b49afb6b9df12206461`; the registry commit tag resolves to
the deployed digest
`sha256:93be0c5cac8d0e0a9af1a21c63ea4d9b775513a11f932e74bb9c27df9b7af43a`.
Diagnostics remain enabled. This is the shutdown-proof/identity candidate,
not the subsequent local position-poll correction.

Measurement11:13:02.150893–11:43:03.385118UTC (18:13–18:43 local) retains897
samples across generations66–69 on one session and control84. Wall span1,801.234s,
maximum sample gap7.933s, zero API read errors. The original classifier hash
`ce60b823674db68a850cafd03fe7e90d77dc8cd0484716bee31b315e6a03e753` and its
guard/unknown rules are unchanged. Quality passes; quote qualification fails.

| Market | Two-sided | Eligible gap | Unknown | Guard excluded | Conservative result |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot |515|20|10|352|515/545=94.50%, fail |
| Perpetual |787|36|14|60|787/837=94.03%, fail |

All three scheduled profile changes return to running. Three raw HALT samples
around the changes remain unknown, rather than being removed as expected
fencing. Forty-five samples are waiting for readiness. For example,
11:17:24–11:17:42UTC shows `reduction_position_unavailable` withdrawing both
markets; both have working quotes again in the11:17:48 sample. The API check at
11:44:31UTC is again waiting on that reason, error null, HALT/pending false.
The engine is available; this is not uninterrupted two-sided quoting.

The exact-window query returns141 WARNING records, below its1,000 limit:
37 supervisor timeout transitions,34 diagnostic stall/recovery pairs and25
slow-stage packets, with zero ERROR, child restart or shutdown-deadline events.
The three observed shutdown totals are1.926s,2.928s and2.508s; proof-before spans
are1.175s,2.121s and1.854s. These are individual observed durations, not a p99
or proof of the unique cause of revision116's historical8s incident.

Of37 probes,11 have a sampled stall within2s;14 have nearby two-sided samples
within4s. Neither proves continuous operation throughout a probe interval.
In-window sampled maxima include controller7.71ms, tick265.28ms, reports356.99ms,
SQL215.63ms and guarded order submission2.592s. Packet retention/rate limits do
not produce a complete per-operation latency distribution. Startup cycle5.17s
and tick307.73ms from earlier packets are outside this window and excluded.

Client-observed API latency also has a tail: status p99/max0.966/6.921s,
liquidity1.412/5.707s. The longest status read starts11:40:53.014UTC and returns
11:40:59.935 with database `server_time`11:40:53.196. This does not localize the
delay between DB observation, API completion, transport and client scheduling.
The old observation remains unknown at receipt; it is not refreshed or excluded.

The local RED test blocks native history while its first position read completes:
a later controller refresh is not serviced until history finishes. The existing
account poll waits for all four branches and its general notifier is already set.
The correction adds a coalescing position request serviced by that same poll
while other branches wait; position reads remain sequential, other branches run
once, and errors/cancellation drain every child. Native response/WS-race proof,
freshness, owner, financial, cap and pricing rules remain unchanged. This proves
an avoidable scheduling dependency, not that every Cloud gap has this cause.

Local qualification:3,541 native tests plus16 subtests, including five new native
poll tests; the35 focused cases overlap that total. Final amd64 image
`sha256:9cf62faa837a1fce9bcbe2ef02c7a3b67bdf85f602388c6e7f569b759b6d7c36`
matches all8 relevant runtime sources. Packaged LONG and SHORT pass partial
application, lost ACK, fee outage/recovery, maker replacement and cold restart;
evidence is `build/e2e-offline/20261112-183259-20b023/` and
`build/e2e-offline/20261112-183259-950bdc/`, manifest
`sha256:f1d605bbdd03e5f7a138d5e61e76d31838497ccea79c75a53e3adc5552771d97`.
This is scoped simulator/local-encryption qualification, not a new full soak,
real Google/GCP KMS or per-fill OKX REST reconciliation. No Cloud configuration,
trading command, DB repair, credential or risk mutation was issued in this run.

Retained evidence: `build/revision118-quote-qualification-20261112/`, including
raw samples, unchanged classifier, exact-window logs, timing correlations and
the separate local source/qualification identities. Next: deploy the local poll
correction and repeat connected qualification; investigate the API tail and
remaining replacement gaps without changing the original target.

## Revision117 thirty-minute baseline during local follow-up

Measurement10:08:30.150467–10:38:31.159119UTC (17:08–17:38 local):901 samples,
1,801.009s wall span, maximum sample gap2.294s, zero API read errors. The same
revision117, session and control84 persist. Original classifier hash
`ce60b823674db68a850cafd03fe7e90d77dc8cd0484716bee31b315e6a03e753` and its
unknown-as-failure policy are unchanged. Quality passes; the target fails.

| Market | Two-sided | Eligible gap | Unknown | Guard excluded | Conservative result |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot |838|5|9|49|838/852=98.36%, fail |
| Perpetual |846|11|10|34|846/867=97.58%, fail |

The three scheduled profile changes recover without intervention. Four raw
HALT samples are transient shutdown fences and remain unknown. Eight unknown
samples per market are unavailable observations around those changes; the
remaining unknowns include reduction-position admission and one reserved
reduction-side observation. No reason is newly excluded to improve the result.
The last sample is running, error null, HALT false. This is quote observation;
there is no new per-fill native financial replay or direct physical-owner DB proof.

The exact-window Cloud Logging query returns103 WARNING records, below its1,000
limit:30 supervisor probe-timeout transitions,30 diagnostic loop-stall/recovery
pairs and8 slow-stage packets; other warnings retain their original messages.
There are zero ERROR, child restart or shutdown-timeout events. Diagnostic
samples use engine process17/owner generation491. Short probe deadlines remain
censored observations, and sampled stages do not establish one unique cause.

Evidence and unchanged analysis are under
`build/engine-diagnostics-followup-baseline-20261112/`. This is deployed5c36bb7bd,
not acceptance of the new local image. The local changes/evidence are in the
[shutdown/freshness follow-up](../performance/mm-quote-replacement-latency.md#shutdown-proof-sharing-freshness-fixtures-and-executor-identity).

## Revision117 verified fence recovery and next profile change

The requested five-minute post-push check starts at09:44:44UTC (16:44 local).
Revision `hummingbot-demo-00117-7gj` is Ready with100% traffic; its service image
tag is commit `5c36bb7bd37e3e7ed01c951dc0b6c78f4c92c985`, resolved digest
`sha256:b25d08252232f232fb77d7a08e59d1d5b1c2e58231083c410c1115b3011bfb87`.
`ENGINE_DIAGNOSTICS_ENABLED=true` remains set. At09:44:47UTC the API reports
running, no error, no HALT/pending mutation, automatic recovery idle with cold
recovery supported, and fresh two-sided quotes for both markets. Session
`5cb18a14-1a35-4388-b2e1-a49b6acc501d` and control84 are unchanged. No manual
Start/reset, DB maintenance, credential/risk or Cloud configuration write was
issued in this verification.

A further200.302-second GET-only observation retains101 samples across the next
scheduled `sideways`→`follow_token` change, generation57→58. At09:48:50.912910UTC
one sample has the shutdown fence; it is absent at09:48:52.910127. Startup and
readiness follow, running returns at09:49:02.922792 and the first new-profile
sample with both markets two-sided is09:49:04.930354. These are observation
timestamps, not exact venue cancellation/replacement intervals. Final sample
09:50:13.000529UTC remains running, no error/HALT and both markets two-sided.

The original conservative classifier is unchanged. Spot:46 two-sided,51 guard
exclusions,3 unknown and1 eligible gap. Perpetual:94 two-sided,4 guard exclusions
and3 unknown. The transient fence/unavailable samples remain unknown; guards do
not become working quotes. This short observation does not satisfy or replace
the original thirty-minute99% target, and does not claim new per-fill financial
or native venue reconciliation evidence.

Deduplicated revision117 logs from09:42:39.546148 to09:49:23.412219UTC contain
25 entries:1INFO and24WARNING, zero ERROR. There are8 supervisor timeout
transitions,6 diagnostic loop-stall/recovery pairs and4 slow-stage packets.
All sampled diagnostic packets use engine process17; no child-restart or
shutdown-deadline event appears in this log slice. Sampled frames include
synchronous startup import, terminal-callback pool pre-ping and terminal SQL.
These remain scoped samples, not a unique cause for every timeout. No recurrence
of the8-second cleanup timeout was observed at this one profile change; its
original cause, the broad-suite liquidity failure and the full quote gate stay open.

Evidence: `build/engine-diagnostics-cloud-recheck-20261112/`, including the
deployment/API snapshots, raw transition samples, unchanged classifier hash and
deduplicated log summary. Runtime code is unchanged during this check.

## Revision116 diagnostics and drained shutdown fence

Revision `hummingbot-demo-00116-vvl` is Ready with100% traffic,2CPU/1GiB,
continuous CPU and min/max1 instance. Its image is tagged with merge2e06d191a,
digest `sha256:c63dc9a06aecb586abbb57d395d8d7bc19cd030baa7b8f1e3448084c7f5b1100`.
All12 qualified runtime sources match that merge's source tree; the earlier
local packaged-image digest is a different artifact and is not reassigned here.
`ENGINE_DIAGNOSTICS_ENABLED=true` is confirmed from the deployed service.
The FE origin `https://hummingbot-nightly.tech-efd.workers.dev` receives HTTP204
for the Authorization preflight under the configured wildcard CORS policy.
This checks CORS, not an actual Google sign-in at the new FE origin.

The API initially observes the revision115→116 owner handoff with a temporary
fence, then revision116 reports fresh two-sided Spot/perpetual quotes in three
successive samples at09:16:54–09:17:14UTC. These successful startup samples do not
constitute a thirty-minute acceptance window.

At09:18:49.844500UTC, Random changes from `follow_market` to `follow_token`,
generation55→56, retaining session/control84. At09:18:57.905631UTC shutdown exceeds
its unchanged8-second deadline with8 tracked orders. The engine exits and the
successor withholds bootstrap at09:19:04.446324UTC because the durable HALT remains.
Subsequent API samples show `RecoveryRequired`/`shutdown_pending`, not working
quotes. This is a real shutdown/recovery incident, independent of Google login.

Read-only PostgreSQL inspection of successor generation490 finds:

- `halt=true`, `halt_kind=shutdown_pending`, no reason, `runtime_active=false`,
  no `shutdown_incomplete` or pending-mutation flag;
- financial and binding hashes match the existing checkpoint;
- zero active submission attempts, creation-pending orders or unapplied fill
  journal entries; no drawdown stop flags;
- the existing native `can_bootstrap` verifier returns true.

Code allows host teardown to finish a second cleanup after the first timeout.
That successful cleanup clears runtime_active but retains its already-existing
HALT. Successor acquisition previously only entered checkpoint verification for
runtime_active/pending work, so this otherwise verified fence was skipped. A
real PostgreSQL RED case reproduces exactly the drained stored state: one
positive recovery case fails while seven protected cases pass. The local patch
also admits a reasonless shutdown fence to the existing verification branch;
it still requires the unchanged checkpoint, account, journal and risk checks.
Manual/reason-bearing HALTs remain protected, and acquiring ownership does not
open submissions: ordinary native startup reconciliation remains required.
This correction was local at the revision116 inspection; its subsequent
revision117 deployment is recorded above. No Cloud DB reset was applied.

Final local checks: all8 new successor cases and the liquidity case pass together
(9 tests);52 service/status/shutdown CLI tests also pass. The broader236-case
PostgreSQL selection records235 passes and one failed liquidity HTTP observation:
the API correctly reports `unavailable` while the fixture expects `observed`.
That unchanged assertion passes in isolation and again with the eight successor
cases. Its exact timing/order cause is not established, so retain the broad
failure rather than label that run green or widen any freshness guard. The
assertion now retains the response body for a recurrence. Hooks pass. No new
packaged-image, soak or connected acceptance pass is claimed for the fence fix.
The final09:32:47UTC API check still shows revision116 halted at control84.

Logs: `/tmp/hbot-drained-shutdown-fence-red.log`,
`/tmp/hbot-drained-shutdown-fence-green.log` (broad failure retained),
`/tmp/hbot-drained-shutdown-liquidity-rerun.log`,
`/tmp/hbot-drained-shutdown-final-focused.log` and
`/tmp/hbot-drained-shutdown-fence-cli.log`.

Initial diagnostic packets are present in Cloud Logging with SQL hooks attached.
Stacks separately show synchronous startup import and native submission-receipt
SQL on the engine loop. Network/controller durations are not interpreted as
continuous loop stalls, and historical slow-span IDs repeated in a packet are
not new events. These samples do not establish one cause for all supervisor
timeouts or prove which cleanup stage consumed the8-second deadline. That
performance investigation and the unchanged thirty-minute99% gate remain open.

Evidence is under `build/engine-diagnostics-cloud-check-20261112/`, including
service/log snapshots, session transitions and the read-only recovery summary.
No Cloud configuration, session/risk settings, credentials or DB rows were
changed. The local SQL proxy only served read-only inspection and is closed
afterward. The prior connected records below remain historical.

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
