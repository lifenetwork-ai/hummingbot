# Cloud Run quote/recovery acceptance — 2026-11-12

Latest full observation (2026-11-14,15:11ICT): revision139/`6cb7e7f56` completes
901 samples on one image/session with quality pass. The exact30-minute window
has161 recorded orders/39 Spot fills, zero ERROR/restart/probe timeout and no
sampled main-thread executor-history fetch. Final health is200 and financial/
binding checkpoint hashes match; bot remains running at control/ack88/88.
A separate startup probe timeout and synchronous financial DB waits remain.
Quote acceptance is still failed: Spot96.51%, perpetual no confirmed eligible
two-sided samples. See [revision139](#revision139-startup-history-deployment-and-observation).

Latest full observation (2026-11-14,14:09ICT): revision138 completes901 samples
on one image/session with quality pass. The bot ends running/control88/88;
startup/live/ready are200, financial/binding checkpoint hashes match and there
are154 recorded orders/33 Spot fills. The exact window has34 WARNING entries,
no ERROR/restart/ownership loss, and one300.875ms probe timeout with a matching
main-loop history-fetch stack. The local startup-history isolation correction
passes TDD/native/PG/packaged checks but is not deployed. Quote acceptance remains
failed: Spot95.24%; perpetual has no confirmed eligible two-sided samples.
See [the revision138 window](#revision138-thirty-minute-probe-correlation-and-local-correction).

Latest deployment check (2026-11-14,13:30ICT): revision138 is independently
verified at100% traffic with commit `912822888`, the outflow retry fix and the
scoped flag still true. Bot is running at control/ack88/88; financial/binding
checkpoints match and health is200. No ERROR/restart/outflow-retry entry appears
in the bounded new-revision log query; four short probe timeouts remain. No new
native cap denial occurs, so connected qualification of that fix stays open.
See [revision138 verification](#revision138-outflow-retry-fix-deployment).

Latest check (2026-11-14,12:44ICT): revision137 remains Ready at100% traffic,
flag true, control/ack88/88 running without HALT or quarantine. Fresh API history
since11:44ICT contains137 Spot fills and at least300 order records (the bounded
order scan is intentionally incomplete). An actual OKX503/cancel-uncertainty
case has automatic proof/resume receipts2.43s apart, without operator recovery.
Financial/binding checkpoint hashes still match. Residual probe warnings and a
rapid Spot-outflow guard retry burst remain; see
[the later health check](#revision137-later-health-check--2026-11-14).

Latest operational update (2026-11-14 ICT): revision137/`8b46a1cee` is verified at
100% traffic with scoped recovery enabled. A complete30-minute API window has901 samples,
no request/status errors and161 recorded orders/12 Spot fills in the interval.
The final bot is running without HALT at control/ack88/88. Spot's conservative
quote result is98.93%, below99%; perpetual has no confirmed eligible two-sided
samples. No uncertainty occurs, so connected scoped recovery remains unproved.
After reauthentication, its exact window has no ERROR/restart and five short
probe timeouts. A later private-WS1006 retry is recorded separately; current
readback is running/no HALT and the financial/binding checkpoint hashes match. See
[revision137 observation](#revision137-operator-enabled-scoped-recovery-observation).

Previously (2026-11-14 ICT), revision134/`e43fde2ab` was verified
at100% traffic. Control/ack88/88 is running without HALT. The read-only DB
snapshot has no pending creations or unapplied fills, and both financial and
account-binding checkpoint hashes match. Scoped recovery remains disabled;
two short probe timeout transitions remain in the bounded deployment query.
This check does not close connected fault qualification or the30-minute99% gate.
See [revision134 verification](#revision134-scoped-lifecycle-fix-deployment).

Previously, revision130/`5bfd54cc0` was deployed. An older50013
uncertain Spot intent blocked startup; the operator-approved scoped maintenance
committed audit278 and Start succeeded at control/ack88. Eighteen samples over
90 seconds are running/ready200/no HALT, and ten new Spot fills match native
quantity/price/original fees. Three probe timeouts remain; no application ERROR
or scheduled restart occurs in the bounded post-Start query. The30-minute99%
gate is not closed. See [the recovery receipt](cloudrun-50013-intent-recovery-20261113.md#applied-and-verified).

Latest 2026-11-13: revision129/`e1b9876ee` is verified at 100% traffic. At
11:22 UTC (18:22 ICT), the bot is running without HALT and Spot has two BUY
and one SELL order. The retained 29m19s observation is incomplete and Spot's
conservative quote result is 97.54%, below 99%. Five probe timeout transitions
remain in that window; another occurs afterward. No ERROR or engine restart
is observed in the bounded log queries. See
[revision129 evidence](#revision129-managed-entry-wakeup-deployment).

Previously, revision128/`d82cd5c28` is verified at100% traffic. The
unchanged30-minute window has9 probe timeout transitions, no ERROR/restart and
no recurrence of the sampled98s cancel-confirmation outage. Spot's conservative
quote result is96.99%, still below99%; missing-WS fault-path qualification is
now verified separately in full-service offline fault injection, with the
original connected gaps retained. See
[revision128 evidence](#revision128-pending-cancel-polling-deployment).

Previously, revision127/`ae553598a` is verified at100% traffic. The
unchanged30-minute observation completes with13 probe timeout transitions
versus19 in the preceding control, no ERROR or engine restart, and continuing
native activity. Spot's98.189s no-quote/cancel-confirmation episode remains a
failure; the99% quote target is not closed. See
[revision127 evidence](#revision127-terminal-read-and-startup-deployment).

The local pending-cancel follow-up reproduces two polling defects and retains
the failed connected gap; see [local TDD](#pending-cancel-polling-follow-up--local-tdd).

Previously, revision126/`492649baf` is verified at100% traffic, including
the terminal-write and checkpoint-batch changes. A connected profile handoff
completes;13 checkpoint spans finish without errors. Eight probe timeouts remain;
there is no observed engine restart or persistent recovery stop. This bounded
review does not close the300ms probe or30-minute quote gates. See
[revision126 evidence](#revision126-terminal-write-and-checkpoint-batch-deployment).

Subsequent operational control observation05:54–06:24UTC retains19 probe
timeouts across three successful profile changes. The local read/startup TDD
follow-up was qualified separately before deployment; see
[the follow-up record](#revision126-thirty-minute-control-and-local-loop-fairness-follow-up).

## Revision139 cash-progress correlation and local correction

The six Spot missing-quote samples from the unchanged07:40–08:10UTC observation
are correlated with25 bounded native order details, with zero truncated timelines.
All six lack SELL liquidity at the sampled instant. They are isolated2s samples,
not six proven multi-second outages. Five next samples are two-sided; the sixth
is guard-excluded after another fill. The original901-sample classifier and all
failed/unknown samples remain unchanged.

| Sample time UTC | Retained observations |
| --- | --- |
|07:42:01.779|Full SELL fills; fresh cash already present; executor-report wait and an overlapping COMMIT diagnostic. Replacement Created events use the same strategy-clock second. |
|07:42:23.771|Full SELL fills at23s, completion at23.398/.430; latest cash at20.051 is still older. Replacement Created events are recorded at24s. |
|07:50:08.112|Prior SELL guard/size rejection, then a BUY fill; two SELL pending creations. |
|07:52:40.246|Full SELL fills and fresh cash at39.490; two pending creations and a financial-flush diagnostic. |
|07:52:58.263|Prior SELL guard; fresh cash covers earlier fills; two pending creations. |
|07:55:22.394|Full SELL fills at21s, completion at22.068; cash at19.335 is still older. Replacement creation is recorded at23s. |

Native Created/fill timestamps are strategy-clock seconds. Completion events
have finer timestamps, but no transport ACK timestamp is available in this
recorder read. Therefore same-second creation is not a measured submission/ACK
RTT, and these rows establish neither one cause for every gap nor exact downtime.
Five unknown samples surround three Random handoffs and retain their original
failure classification, including temporary shutdown fences.

The controller correctly requires fresh cash for both assets before handing off
a full Spot entry. Code inspection and native RED tests show that, when the first
full-entry report precedes those observations, the later cash update does not
wake the report path. The local correction adds one connector asyncio.Event and
one managed-Spot listener. It reuses the existing25ms coalesced native report
publisher only after the unchanged cash predicate covers the full fill. Native
financial commit/controller handoff still owns capacity; no event submits an
order or grants trading authority. A second RED catches repeated publications
from a steady account stream; the listener deduplicates covered full entries and
retains only current resident entries. No new periodic reporter is introduced.

Tests cover split base/quote observations, stale/invalid cash, pre-fill/burst
updates, current session/generation, removed entries, revoked admission/control
errors, failed reports retaining ordinary clock retry and Stop draining the
listener. Initial RED is2 failures for missing progress; the steady-stream RED
is1 failure for three unwanted reports. Final native regression passes3,757 tests
+16 subtests, including16 account-progress cases; hooks and diff checks pass.

The final amd64 candidate has config digest
`sha256:fd9e1cde0fb936790e92a57d8c01b9d829716d09275bf53b7e884ff9096c21bd`
and registry manifest
`sha256:2d069850358e63f1b38fbe7f1082c1d72b312c4b0e737e22b29756b250a61441`.
All three changed runtime source hashes match the workspace and packaged image.
Two scoped full-service E2Es on this manifest pass at2CPU/1GiB: Spot outflow
retains original fee/rebate, committed partial fills, funded SELL progress and
no local cap-denial retry; until-stopped Random rollover/overlapping-owner
handoff/cold restart/Stop retain the same session/control and four exact fills.
The successor increments owner generation once and proves eight canceled orders
despite16 stale pending-list rows. Test containers are removed. These are scoped
regressions, not a latency A/B, full load soak or connected acceptance.

The15:43:30ICT readback still observes revision139 at100% traffic: running,
control/ack88/88, no HALT/pending mutation or active incident; Spot has2 BUY/1SELL,
perpetual2 SELL. This code is not deployed. An auxiliary GET to the unsupported
`/liquidity` path returned403; the correct `/statistics/liquidity` GET succeeds.
That observer mistake is separate from the original complete acceptance window.
The correction does not establish a deployed quote-latency improvement or a99%
pass. Commit/deployment and a new unchanged30-minute window remain required.

### Perpetual policy readback

A separate read-only DB snapshot keeps financial/account-binding hashes matched,
owner526/control88 and no HALT. Remaining LONG basis is0.0498BTC with principal
3630.892648288816USDT and unallocated opening fees0.726178529658USDT. Two
acknowledged SELL CLOSE intents each cover0.0002BTC at72938.67, with HTTP200/
code0/sCode0 receipts. Canonical payload hashes independently match the saved
attempts: post-only SELL, `posSide=long`, contract size0.02 at0.01BTC/contract.
They are closing LONG, not opening SHORT. The read-only SQL proxy is closed.

The08:35:36UTC API observation has venue/owned LONG0.0498, two working SELLs,
closing reservations0.0004, fresh fee-readiness and market mid67074.95. BUY remains
inventory-limited. Illustrative closing math at a0.0002 fee rate (0.02%,2bps) and
Demo tick0.01 reproduces72938.67. That rate is a math input, **not** an independent
authenticated current fee read; native fee validation remains authoritative.
Live instrument metadata is not substituted for Demo tick rules.

No new perpetual fill occurs in the revision139 observation. Real post-fill
handoff therefore remains unqualified; lowering the quote below fee-inclusive
breakeven, forcing a trade or changing caps would violate the current policy.
No deployment, Start/Stop/Recover, credential, DB maintenance or risk-policy
mutation is performed in this investigation.

Evidence (ignored/private): `build/quote-gap139-review-20261114-1515/`,
`build/e2e-offline/20261114-154221-103724/` and
`build/e2e-offline/20261114-154348-4356fa/`. Preliminary E2Es on the earlier
non-deduplicated local candidate are retained separately and do not qualify the
final source. The final results above use the same final manifest.

## Revision139 startup-history deployment and observation

Cloud Run independently reports `hummingbot-demo-00139-98h` Ready at100% traffic
since07:38:28.484UTC (14:38:28ICT). Artifact Registry maps full commit
`6cb7e7f560644a1b8995d610ecc3a0e48242e5d7` to the running digest
`sha256:66844ca05f39fb3d383ceec1b5b08c6950f6a9946619f215b36a30223b0e60f1`.
All non-image container configuration matches revision138:2CPU/1GiB, scoped
recovery true, Demo/postgres_kms routing, existing probes and CPU always allocated.
No deployment, control, financial or risk configuration is changed by this observer.

Initial readback is running, owner available, same until-stopped session,
control/ack88/88 and no HALT/pending mutation. Startup/live/ready are200. Spot
has working BUY/SELL; perpetual SELLs work while BUY is inventory-limited. A
read-only repeatable-read DB check has owner526, watermark46997, matching
financial/binding checkpoint hashes, zero pending creations/unapplied fills,
no prepared/possibly-sent/uncertain attempts, three positions and risk-stop false.
The temporary SQL proxy is closed after the reads.

The pre-window revision log slice has one300.795ms supervisor timeout at
07:39:44.379UTC. Its earlier sampled main-thread stall at07:39:35.879UTC is in
`restore_market_states`/SQLAlchemy instance loading/weakref cleanup, not the
history `fetchmany` path. It is outside the exact probe wait and is not proof of
that timeout's cause. Later samples still show physical ownership/pre-send
recording/flush/COMMIT waits. Read isolation does not remove financial writer I/O.
The initial tracker-size read is small (two perpetual entries/1068bytes and
three Spot entries/1610bytes); there is no evidence here of a large tracker blob.
Selected executor history has28,921 rows across the two managed controllers.

The unchanged v3 classifier/2s paired reader measures07:40:57.614–08:10:59.142UTC
(14:40:57–15:10:59ICT):901 samples, wall span1801.315s, maximum gap2.427s and no
request/identity failures. One revision139 and the same until-stopped session
cover three Random handoffs (generations99–102). Two transient shutdown-fence
samples clear on the next sample; no technical HALT requires recovery. All five
unknown samples/market remain failures; guards are never credited as uptime.

| Market | Two-sided | Eligible gaps | Guard exclusions | Unknown | Conservative result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot |304|6|586|5|96.51%, below99% |
| Perpetual |0|0|896|5|No confirmed eligible two-sided samples |

Spot's conditional result is98.06%, also below99%. Reasons include size/allocation,
outflow and an18-sample reference-deviation pause; perpetual remains inventory-
limited. The quote/release gate stays open. Revision138's95.24% and this96.51%
have different eligibility/market mixes and do not prove a throughput improvement.

The exact bounded log query returns27 WARNING entries, zero ERROR entries,
zero scheduled restarts/ownership-loss entries, zero50004 entries and zero
supervisor probe timeouts. Eight sampled loop stalls (heartbeat gaps254–290ms
at detection) remain in COMMIT, owner checks, intent/transport-receipt recording,
flush and primary-key lookup. No sampled main-thread stack contains the old
executor-history initialization/fetch. These frames are sampled evidence, not
complete blocking-duration measurements or a guarantee of zero event-loop delay.
The separately retained pre-window300.795ms timeout is not silently discarded.

Final API readback is running, no HALT/pending mutation, startup/live/ready200.
Complete one-page history contains161 recorded orders (103 Spot,58 perpetual),
39 Spot fills and no provisional creation. These are bounded recorder records,
not an exchange-wide live snapshot. Final repeatable-read DB verification keeps
owner526/control88/88, watermark47230, three positions and risk-stop false;
financial/binding checkpoint hashes match, with zero pending creation,
unapplied fill or prepared/possibly-sent/uncertain attempt. Four attempts remain
acknowledged. The read-only proxy is closed after the check.

This verifies rollout/continued operation and completes the planned comparison
window. It does not close the99% quote, residual DB-wait, connected native-cap-
denial or uncertain-BUY quarantine lifecycle gates. No operator Recover/Start,
secret, migration, Cloud resource or risk-policy change is made during observation.

Evidence (ignored/private): `build/startup-history-postdeploy-20261114-1440/`.
Existing failed observations below retain their own revision and scope.

## Revision138 thirty-minute probe correlation and local correction

The unchanged classifier measures06:38:53.587–07:08:54.943UTC
(13:38:53–14:08:54ICT),901 paired samples, sampled wall span1800.818s and maximum
sample gap2.382s. There are no request or identity failures: one revision138,
one until-stopped session and control/ack88/88. Three temporary shutdown fences
are sampled at Random handoffs; all clear on the next two-second sample. These
are not technical HALTs, and their unknown samples remain failed observations.

| Market | Two-sided | Eligible gaps | Guard exclusions | Unknown | Conservative result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot |220|3|670|8|95.24%, below99% |
| Perpetual |0|0|893|8|No confirmed eligible two-sided samples |

Spot's conditional result excluding unknowns is98.65%, also below99%. Guards
are reported separately and never credited as quote uptime. Spot reasons include
size/allocation and outflow; perpetual is inventory-limited. This run is not a
release/SLO pass, and its changing eligibility mix does not prove a regression
or improvement from the previous revision's percentage.

The exact bounded log query returns34 WARNING entries, zero ERROR entries,
zero scheduled restarts and no ownership-loss entry. Eight warning entries
contain OKX50004 status-read timeouts; they are log entries, not a count of unique
requests. One supervisor probe fails at06:58:10.468UTC, client wait300.875ms,
one failed probe and zero accumulated failure seconds. A sampled main-thread
`fetchmany` stack at06:58:10.219UTC lies inside that wait; heartbeat does not
advance during capture. Earlier pre-window import, ownership and commit stack
samples remain separate causes/evidence, not a universal explanation.

Final health is200 for startup/live/ready. Bounded API history is complete in one
page each:154 recorded orders (94 Spot,60 perpetual),33 Spot fills and zero
provisional creations. Recorder history is not an exchange-wide live snapshot.
The read-only repeatable-read DB check finds physical generation525, checkpoint
watermark46491, matching financial/binding hashes, no pending creation,
prepared/possibly-sent/uncertain attempt or unapplied fill; four attempts remain
acknowledged, three positions retained and risk-stop false. The SQL proxy closes
after the read. No control, Cloud config, financial row or risk policy is changed.

TDD isolates the managed startup history reduction on a worker-owned session;
strategy construction and the existing physical owner/control/credential gate
remain on the engine loop. Stop closes the current read without scanning the
remaining history, including repeated cancellation. Final validation is3,741
native tests +16 subtests,14 focused tests, eight disposable PostgreSQL selections,
two packaged E2Es and hooks. Candidate config`e00b5b350b890655fef552d6d9c8488bff1eff0fda9771c629e47fe6a9318a6a`,
manifest`b7dc6730854fa50ffc538a599aa14270ca35ecbe1692c68ef570ce5099dc2c5e`.
The packaged until-stopped/Random/cold/overlapping-owner-handoff/Stop and delayed
account-startup scenarios both pass. At this observation the candidate was
qualified locally and not yet deployed; the window above measures commit
`912822888`, not this correction. Revision139 qualification is recorded above.
Cold import/physical writer waits, connected native outflow denial and the
uncertain-BUY quarantine lifecycle remain open. No threshold or policy is relaxed.

Evidence (ignored/private): `build/probe-correlation-20261114-1338/` includes the
unaltered classifier/raw samples, exact bounded logs, timing correlation,
read-only financial check, TDD replay, final source hashes and test receipts.
See [the implementation and limits](../performance/mm-quote-replacement-latency.md#revision138-startup-history-isolation--2026-11-14).

## Revision138 outflow retry fix deployment

Cloud Run reports `hummingbot-demo-00138-862` Ready at100% traffic since
06:25:19UTC (13:25:19ICT). Artifact Registry maps exact commit
`91282288824df60370e0353481dfb5bb7fed0046` to the running image digest
`sha256:033126063c3627aa00e16e3961d2c271e4d796f9eea226edc24b122b3fd701a7`.
Apart from image, container env/resources/probes/command/mounts match revision137:
scoped flag true, Demo routing,2CPU/1GiB. No Cloud setting, trading-control or
financial change is made by this observer.

Initial and final checks report running, control/ack88/88, no current HALT,
pending mutation or quarantine. The unchanged until-stopped session survives
deployment. Spot has working BUYs while SELL size/skew is blocked; perpetual has
working SELLs while BUY is inventory-limited. Startup/live/ready return200 at the
health checks. The old05:14 automatic-recovery receipt remains historical idle
state; its retained order ID is not a new revision138 incident.

A complete120s API observation has61 samples, no request failures and no
revision/control change, from06:28:08.649 to06:30:09.069UTC. It begins at the
generation92 profile transition: one temporary shutdown fence clears in the next
sample, followed by one starting/four readiness-wait samples and56 running
samples. Final status is running/no HALT. These transitional observations are
not relabeled as successful quoting. This short check does not replace the
existing30-minute99% classifier or prove uninterrupted engine ticks.

A read-only repeatable-read DB transaction confirms owner generation525,
matching financial/binding checkpoint hashes, watermark46213, three positions,
four acknowledged attempts, zero pending creations and zero unapplied fills.
There is no prepared/possibly-sent/uncertain attempt or risk-stop flag. The
temporary loopback SQL proxy is stopped afterward. Bounded complete API history
since Ready contains20 order records (10 Spot/10 perpetual),16 cancelled and4
created at the initial history read, none provisional. The repeated bounded
history read through06:30:09 has those20 records cancelled as quote refresh
continues; it is not a current open-order snapshot. No fill is recorded in the
bounded interval.

The bounded revision138 logs contain no ERROR, restart-scheduled, service-stop,
ownership-loss or native `spot_outflow_limit` denial. Four supervisor probe
timeouts at300.794–300.910ms remain against the unchanged300ms budget. Loop
stall/recovery diagnostics are retained separately; this does not establish a
probe root cause or a latency improvement. Without a new native cap denial,
absence of retry spam does not qualify the connected rejection path. The old
revision137 burst and existing quote/probe failures remain preserved.

Evidence (ignored/private): `build/cloudrun-outflow-postdeploy-20261114-1326/`
contains immutable revision/tag proof, revision-filtered logs, paired health
checks, the short observation, bounded history and sanitized DB preflight. The
observer only performs reads; no cap reset, HALT reset, Stop or Start is issued.

## Revision137 operator-enabled scoped recovery observation

The initial API observation was collected while gcloud reauthentication was
pending. After the operator reauthenticated, Cloud Run independently confirms
`hummingbot-demo-00137-c8z` Ready at100% traffic, with
`MM_SCOPED_RECOVERY_ENABLED=true`, Demo routing and `postgres_kms` credentials.
Artifact Registry maps commit `8b46a1cee51be1c2f91ab5b6f903c2980643f077` to its
running digest
`sha256:bf9bfd8236d6be1a428091747f6ab3b4f71e35508719679536f39fabb295d574`.
Resources are2CPU/1GiB; startup/liveness use `/startup` and `/live`, not trading
readiness. Container configuration matches revision134 except image and the
scoped flag; no other env variable differs. The immutable revision metadata
verifies the flag for the earlier observation, not merely the current idle status.

The unchanged conservative classifier observes19:37:44.686–20:07:46.125UTC on
November13 (02:37:44.686–03:07:46.125ICT on November14):901 samples over1801.439s,
maximum sample gap2.446s, no collection/identity failures. Window quality passes.
The same until-stopped Random session and control/ack88/88 remain; its profile
generation advances26→29. No control, exchange, Cloud configuration or DB writes
are made by this observer.

- Spot:554 two-sided,341 guard exclusions,6 unknown and no confirmed eligible
  gaps. Conditional quoting is554/554, but the unchanged conservative result is
  554/560 =98.93%, below99%. Guard exclusions remain separate from successful
  quoting; reasons include order-size/allocation, readiness and Spot outflow.
- Perpetual:895 guard exclusions and6 unknown, with zero confirmed eligible
  samples. Its two-sided target remains unqualified. Inventory limits dominate;
  this is not presented as zero-percent trading availability.

There are889 running,3 starting and9 waiting-for-readiness observations, with
no `status.error`, stale-service sample or pending-mutation observation. Three
samples expose the explicitly temporary shutdown fence during profile handoff;
each clears in the next2s sample. They and the corresponding three starting
samples remain unknown/failing observations. Each handoff reaches running again
about10s after its first fenced sample. No manual reset or Start is issued.
The subsequent exact-window Cloud Logging query confirms no ERROR, scheduled
child restart, service-stop or ownership-loss entry in that bounded interval.

Bounded, complete API history pages for the exact interval contain161 order
records (101 Spot/60 perpetual), all with exchange IDs and none creation-pending
at readback. Recorded statuses are148 cancelled,10 completed and3 created.
There are12 recorded fills, all Spot; none are perpetual. These are recorder
observations, not a new exchange-wide reconciliation or independent venue-fill
comparison. `/startup`, `/live` and `/ready` return200 at both bookends. Final
status is running/no HALT/no pending mutation, with automatic recovery idle,
zero quarantined orders and zero reported reservations.

No sample enters isolated recovery. Ordinary quoting and handoff observations
cannot close lost-ACK/late-fill/Disconnect/OOM-under-quarantine connected gates
or prove no duplicate POST under uncertainty. Those gates and the original
quote/probe targets remain open. External API p99/max is269.08/544.36ms for
status and257.53/546.13ms for liquidity; these are HTTP observation timings,
not engine-probe or order-placement timings. No probe threshold is changed.

### Log and checkpoint verification after reauthentication

The exact observation window has39 WARNING entries: five supervisor probe
timeouts,16 slow-stage diagnostic reports, nine loop-stall and nine loop-recovered
reports. Probe waits are300.659–300.799ms against the unchanged300ms budget;
each reports a single failure and zero accumulated failure seconds. Four occur
near profile handoffs and one occurs mid-profile. This temporal association does
not establish one blocking frame or root cause. Retained slow-span lists can
repeat earlier spans; they are not summed as fresh stalls. The probe gate remains
open, and external API timings are not substituted for engine-loop latency.

The separate subsequent log query (records20:08:09–22:45:26UTC) has19 further
probe timeout transitions and one ERROR at20:37:34.619UTC: the perpetual private
WebSocket closes unexpectedly with code1006. The native listener catches it
and enters its existing5s retry path. No scheduled engine restart, service-stop,
ownership-loss or technical-HALT entry appears in this bounded query. Exact
reconnection time is not available in retained logs; current readiness is not
claimed as a timestamped reconnect receipt. Do not describe the whole revision
as ERROR-free based on the earlier clean window.

At22:45:32UTC (05:45:32ICT), fresh status is still running on revision137,
control/ack88/88, no HALT/pending mutation/availability incident and automatic
recovery idle with no quarantine. Spot has working BUYs, with SELL size/inventory
skew blocked; perpetual has working SELLs with BUY inventory-limited. Drawdown
is fresh and below its unchanged limits.

A repeatable-read, read-only DB transaction confirms both financial and
account-binding checkpoint hashes match, watermark40196, three positions,
four acknowledged attempts, zero pending creations, zero unapplied fills and
no risk-stop flag. No prepared/possibly-sent/uncertain attempt is present.
The temporary loopback Cloud SQL Auth Proxy is stopped after the read. No
financial/credential/control maintenance or Cloud change is performed.

Evidence (ignored/private): `build/cloudrun-scoped-enabled-20261114/` retains the
901 raw paired observations, unchanged classifier/hash, quality/quote analysis,
transitions, bounded history pages, final status/health, the original gcloud error
receipt, successful immutable revision/tag/traffic verification, bounded log
queries and the sanitized DB preflight. The earlier revision134 credential
reference was used only for initial read-only API authentication; it was never
treated as evidence of revision137 configuration.

## Revision137 later health check — 2026-11-14

At05:44:40UTC (12:44:40ICT), Cloud Run still reports revision137 Ready at100%
traffic, with the same pinned digest,2CPU/1GiB and scoped flag true. Status and
engine observation agree: running, owner available, control/ack88/88, fresh
observer, no current HALT/pending mutation/availability incident/quarantine.
Startup/live/ready each return200. The same until-stopped session is at
generation87. Spot has working SELLs with BUY size/skew blocked; perpetual has
working SELLs with BUY inventory-limited. Funding and drawdown are fresh; no risk
limit is breached. No control or configuration changes are made by this check.

A bounded history read since04:44:14UTC returns137 Spot fill records, timestamped
04:50:29–05:41:35UTC. There are at least300 order records (217 Spot/83 perpetual)
before the six-page order budget is reached. Do not present300 as the complete
hour total. None of these300 rows is creation-pending. A separate repeatable-read,
read-only DB snapshot confirms the global pending-creation and unapplied-fill
counts are both zero, with four acknowledged attempts, three position rows,
matching financial/binding hashes and checkpoint watermark45768. There is no
prepared/possibly-sent/uncertain attempt or risk-stop flag. The temporary SQL
Auth Proxy is closed afterward.

A final GET at05:49:58.881UTC (12:49:58.881ICT) reconfirms revision137 running,
control/ack88/88, fresh observation, no HALT/pending mutation/quarantine and
automatic recovery idle.

### Actual transient cancellation recovery

At05:14:12.356UTC, OKX order-status GET returns HTTP503/code50001, service
temporarily unavailable. At05:14:17.433UTC, the native connector logs cancellation
outcome awaiting reconciliation for one Spot SELL. Its runtime audit receipts
are306 (pause05:14:17.345775),309 (positive order proof05:14:19.729106) and310
(authority reverified/resume05:14:19.777427). Pause-to-resume is2.431652s;
first logged GET failure to resume is about7.42s. These are distinct intervals.

The order detail has its original exchange ID, status `OrderCancelled`, no
pending creation and zero recorded filled amount. Current automatic recovery is
idle and owner generation remains524. This is evidence of automatic positive
cancel proof/resume without a manual HALT reset or Start. It is an uncertain
SELL cancellation, not the supported uncertain-BUY/funded-SELL quarantine case;
do not close the latter connected gate from these receipts. Recorder/audit state
alone is also not a complete exchange POST-attempt trace.

### Residual warnings and a retry burst

The most recent one-hour query contains111 WARNING entries and no ERROR:
14 supervisor probe timeouts at300.759–301.411ms,26 slow-stage diagnostics,
30 loop-stall/30 loop-recovered reports and11 other warnings. No restart-scheduled,
service-stop, ownership-loss or incident entry appears in that bounded query.
Probe deadlines and coarse diagnostic maxima still do not establish one root
cause. The latency gate remains open.

The eight-hour ERROR query is not clean: one perpetual private-WS1006 closure
at04:38:23.854UTC enters its existing retry handler. Another23 ERROR entries
at02:28:32.164–02:28:32.563UTC report the protective `spot_outflow_limit` denial
across12 local order IDs, including executor retry counts0–10. Those are service
guard denials, not proof of12 rejected venue POSTs or an exchange outage.
The approximately0.40s retry burst is an operational follow-up: reproduce it with
TDD and verify guard-aware retry pacing/diagnostic severity without loosening the
outflow cap. This health check identifies that issue; it does not implement or
qualify its fix. No financial HALT is currently present, but do not label the
revision completely free of errors or operational inefficiency.

Evidence: `build/cloudrun-check-20261114-1244/` retains authenticated status,
health, immutable metadata, recent/ERROR log queries, target-order log/audit/
detail receipts, bounded history pages and the read-only DB preflight. No runtime
source, account policy, credentials, risk limit or financial row is modified.

## Revision134 scoped lifecycle fix deployment

Revision `hummingbot-demo-00134-dkx` is Ready and serves100% of traffic.
Artifact Registry maps the exact commit
`e43fde2ab2b11a65cccb00d5fdd3e498c35b544a` to its running digest
`sha256:668ded4b008f7a81e4fdcb696ec58be0d6247b4c58421b0bb90ce91411a6b444`.
Resources remain2CPU/1GiB, OKX is Demo, credentials use `postgres_kms`, and
`MM_SCOPED_RECOVERY_ENABLED=false`. No Cloud configuration, trading-control or
DB mutation was made for this verification. Authenticated observations use the
existing Basic admin identity, not a Google identity impersonation.

At18:50:59UTC on November13 (01:50:59ICT on November14), control/ack88/88 is
running with a fresh service observer, no current recovery block, no availability
incidents and no drawdown breach. The same until-stopped Random session remains
active and advances from generation21 to22. Spot observations include working
BUY/SELL quotes and a short submission-pending transition after the profile
change. Perpetual has working SELL orders; new BUYs are blocked by
`inventory_limit`. This guard exclusion is not evidence of a two-sided quote pass.
The historical failed recovery-check receipt is retained; it is not a current
HALT and was not reset.

A repeatable-read, read-only transaction through a temporary loopback Cloud SQL
Auth Proxy confirms zero pending creations, zero unapplied journal fills,
three position rows, four acknowledged submission attempts and no
prepared/possibly-sent/uncertain attempts. The current checkpoint's financial and
binding hashes match, its watermark is37924, and no risk-stop flag is set.
The proxy was stopped after the read. `/startup`, `/live` and `/ready` return200.

The bounded revision134 log query starting18:38:30UTC contains no ERROR or
scheduled engine restart, but retains two supervisor probe timeouts at300.737ms
and300.856ms against the existing300ms budget. Both report one failed probe and
zero accumulated failure seconds. Separate loop diagnostics record recovery;
they do not establish an exact causal duration for each probe. No latency
threshold was widened. This short deployment check is not a stable30-minute
acceptance measurement and does not enable the experimental feature or close
the remaining fault matrix.

Private evidence (ignored): `build/cloudrun-scoped-20261114/` contains revision
and image-tag proof, health/status observations, the sanitized DB preflight,
short follow-up samples and revision-filtered logs. Secret-bearing revision
metadata is not copied into this report.

## Revision129 managed entry wakeup deployment

Revision `hummingbot-demo-00129-skx` serves 100% of traffic with commit
`e1b9876ee9692701c59013d1ee2444d3e51f1a69` and image digest
`sha256:3b967e83eb6dc6d5c79a7282c7b5c093c1a3d9a1ccb708ea44668ec1382cdb25`.
Environment, resources, probes, command, arguments and mounts match revision128.
This verification makes no Cloud configuration, DB or trading-control changes.

The observer retained 880 samples from 10:33:58.595657 through
11:03:17.839217 UTC: 1,759.24 seconds (29m19s), with maximum sample gap 2.302s
and no collection failures. The local observer was interrupted before completion;
`completed_at` is null and the canonical quality and target gates remain false.
Later status checks do not fill that missing measurement interval.

Using the unchanged conservative classifier:

- Spot: 675 two-sided, 8 eligible gaps, 188 guard exclusions and 9 unknowns;
  675/692 = 97.54%, below the unchanged 99% target. Gap stages comprise five
  executor-activity, two pending-submission and one proposal-confirmation sample.
- Perpetual: 871 guard exclusions and 9 unknowns, with no confirmed eligible
  samples. Its two-sided target remains unqualified, rather than a zero-percent
  availability result.
- Three raw HALT flags occur only in stale, mismatched session-transition views.
  These observations remain unknown; subsequent fresh views clear without a
  reset. Control/ack remain 86 across three scheduled profile transitions.

The exact retained-window log query has five probe timeout transitions,
18 loop-stall/recovered pairs and 16 slow-stage warnings, with no observed
ERROR or restart. Diagnostic counter deltas include 1,716 strategy ticks,
4,232 controller cycles, 172 completed submissions and 142 completed cancels,
with no counted errors. These cumulative deltas demonstrate continued activity,
not uninterrupted ticks. Stack samples include owner reads, other SQL and
commits; they do not establish one shared cause for every delay. Compared with
revision128's nine probe transitions and 96.99% quote result, duration and market
conditions differ, so this is not a controlled performance improvement claim.

The separate 11:03:17–11:07:54 UTC log query retains one additional probe
timeout, one `spot_outflow_terminal_unavailable` incident (`operator=False`)
and an admin-access-denied warning, with no ERROR/restart. The incident therefore
remains visible rather than being omitted from the result. At 11:10:12 UTC,
all seven health/admin reads return HTTP200; fresh status is running, no HALT,
no pending recovery mutations, Spot 2 BUY/2 SELL and Perpetual 0 BUY/2 SELL.
This later status does not prove the incident's precise recovery duration.

Evidence: `build/entry-wakeup-postdeploy-20261113/`, including the unchanged
classifier output, exact-window and post-window log summaries, final diagnostic
counter summary and `final-check/summary.json`. Private deployment metadata and
raw responses remain ignored. Deployment is verified; the full uninterrupted
30-minute qualification, 99% quote target and residual probe investigation stay
open.

## Revision129 fill-gap correlation and local correction

Read-only history queries cover all 84 Spot orders created between 10:32 and
10:52 UTC and 33 selected order timelines around the eight failing samples.
All eight samples overlap native fill-completion/replenishment activity. Each
next two-second sample is two-sided again. This is a sampled recovery bracket,
not a measurement that every outage lasts exactly two seconds.

| Gap sample UTC | Missing side / observed stage | Nearby recorder evidence |
| --- | --- | --- |
| 10:34:58.529 | SELL / submission pending | Filled entry completes at 10:34:57.678; a concurrent zero-fill cancel and replacement entries appear at 10:34:57–58. |
| 10:35:04.529 | SELL / executor activity | Two entries complete at 10:35:04.494–.613; replacements are recorded at 10:35:05. |
| 10:35:46.547 | SELL / submission pending | Two entries complete at 10:35:45.694–.745; replacements are recorded at 10:35:46. |
| 10:39:34.706 | BUY / proposal confirmation | Two entries complete at 10:39:33.407–.463; replacements are recorded at 10:39:34. |
| 10:41:22.778 | BUY / executor activity | Two entries complete at 10:41:22.673–.722; replacements are recorded at 10:41:23. |
| 10:51:15.262 | SELL / executor activity | Two entries complete at 10:51:15.251–.301; replacements are recorded at 10:51:15. |
| 10:51:19.290 | SELL / executor activity | Two entries complete at 10:51:19.047–.106; replacements are recorded at 10:51:20. |
| 10:51:43.290 | SELL / executor activity | Two entries complete at 10:51:43.158–.208; replacements are recorded at 10:51:44. |

Recorder creation/created-event timestamps can use the engine clock and have
one-second resolution. They are not precise transport ACK timestamps, and the
separate API reads are not an atomic snapshot. The bounded all-severity log read
around the first three gaps retains diagnostic warnings but no per-order INFO
timeline. We therefore do not derive subsecond replacement latency or claim
one proven cause for every historical sample.

Code exposes a reproducible missing wakeup: a completed managed maker entry
wakes its own loop, but remains active until the controller authorizes its
position handoff. The existing immediate publication path accepts only a closed
executor. Thus the full-fill report can wait for the next ordinary strategy tick.
Three valid RED cases reproduce absent publication for BUY, SELL and a coalesced
burst. The correction requests the existing bounded 50ms report publication
after a matching native full-entry callback. Exact full-fill checks, resident
executor/current session-generation checks and shutdown rejection remain in place.
Reports run outside the event callback with an empty authority context. The
controller still requires a fresh account observation; the executor stays active
and reserves its level until the normal financial handoff. A failed report does
not release capacity, and the regular clock remains its retry path.

A separate two-case RED reproduces the generic Spot terminal-unavailable warning:
`reconcile_terminal` processes at most four candidates. A remaining backlog or a
new arrival during an awaited successful proof has not necessarily failed any
proof. Previously the service treated every nonempty queue as a failure and
could inherit a 24–30s retry delay from earlier failures. It now applies failure
backoff only when a remaining candidate has an actual recorded proof failure.
Unattempted work retains reservations and the BUY-only wait on the ordinary
one-second poll cadence. Actual failed proofs retain their stage and backoff.
This reproduces a mechanism consistent with the generic warning, not a complete
reconstruction of the historical queue, which was not captured.

Probe correlation remains separate: only one of eight quote-gap sample times is
within one second of a probe timeout (10:35:46). Six retained loop stacks show
owner reads; other stacks show SQL, COMMIT, pool ping/rollback or startup report
loading. These synchronous waits can delay trading work on the same loop. The
patch does not remove them, cache ownership authority, change probe thresholds
or bypass durable write fencing. A zero-timeout claim and the 99% quote gate
remain open.

Evidence: `build/quote-gap-review-20261113/` contains bounded history reads,
`gap-history.json`, `gap-correlation.json`, valid RED outputs and regression
outputs. The local changes are not yet deployed; revision129 remains the measured
baseline and its failed/incomplete gates are retained.

Validation: the complete `test-cloudrun-regression` lane passes 3,691 tests plus
16 subtests. The scoped disposable PostgreSQL run passes 29 tests; the focused
Spot/refresh/wakeup run passes 127 tests. Hooks pass. At 11:22:21 UTC, a fresh
read on revision129 still shows running/no HALT, Spot 2 BUY/1 SELL, Perpetual
0 BUY/2 SELL, zero pending terminal Spot orders, BUY allowed and no active
service incidents. This confirms the earlier Spot incident is no longer active;
the precise clearance time is not retained. See `status-1822-summary.json` and
the private status snapshot. No connected acceptance result for the local patch
is claimed; deploy it before repeating the full 30-minute window.

## Revision128 gap analysis and entry scheduling follow-up

All21 failing Spot observations are retained:12 `executor_activity` gaps without
pending create/cancel, two `submission_pending` gaps, four stale transition
views and three startup unknowns. Most executor gaps are minutes away from the
nearest probe transition; probe slowdown is not demonstrated as their common
cause. The stored snapshots lack a per-executor terminal/publication trace, so
this classification does not uniquely attribute each historical sample.

Code inspection finds a separate reproducible scheduling defect in managed
maker PositionExecutor entries: stop can arrive during the normal periodic
sleep, and a native terminal callback can finish the inner shutdown wait only
to encounter the outer periodic wait. Five valid RED cases fail before the
correction. The patch wakes that outer wait with the existing shutdown order-
change event and wakes it on managed early stop. It retains the configured
periodic timeout and existing shutdown/financial path. Nine final cases cover
BUY/SELL, exact partial/zero holds, pending proof, duplicate/foreign events and
legacy/unconfigured/taker cadence. No fresh-account, commit, cap, breakeven or
terminal-proof gate is relaxed; this is not a claim to explain all12 samples.

The new `pending-terminal-ws` offline scenario uses the exact deployed128 image
(`8ac9faa9…`, image config `a716ffb3…`) on an internal Docker network with dummy
keys, real service supervisor/connectors/API and isolated PostgreSQL. Two CPU /
1GiB are enforced. Ordinary20s fixture refresh terminates eight original native
quotes; two full fills are withheld from REST while six zero-fill cancels remain
visible. Twelve terminal WS frames are dropped, with unrelated current account
frames keeping both private streams alive. Bounded measurement wrappers call
unchanged native methods and observe10 actual short-cadence selections plus two
native account-worker entries per connector. Each hidden fill is requested at
least twice. Native orders and matching controller levels remain reserved until
fill evidence is released; no terminal state is invented from a cancel ACK.

After release, all eight original terminal records are committed, both quote
sides return, and the independent venue ledger matches exactly two recorder
fills, original fees and contract/base units. The observed release-to-quotes
interval is4.919s, including test polling/SQL/export work; it is not a venue
latency benchmark or a5s completion guarantee. Owner UUID/generation, session and
engine PID stay unchanged; there is no duplicate submission. Stop drains working
orders through the normal API. This is full-service offline branch proof for
the deployed polling fix, not a connected missing-WS injection or a99% pass.

The first E2E attempt fails startup because the new test wrapper referenced its
role before initialization; its evidence remains failed. The corrected run
passes without widening startup, probe or terminal deadlines. The simulator's
11 valid RED cases fail against its prior implementation and pass after adding
bounded targeted faults, immutable venue truth and exact fill-view restoration.
Shipping native regression3,674 +16 subtests and38 related disposable
PostgreSQL tests pass; all changed-file hooks and whitespace checks pass.

The packaged entry-wakeup candidate, config digest
`sha256:f11a28613e16f4ce00abd80ff0601e98ba5ea2f50aa2e9a44ae431f806dce512`
and temporary loopback-registry manifest
`sha256:ede135540b9f823994e62eb9fcf520dc0a198ece824a038e6e17a7df30e291d2`,
passes the same full-service scenario at2CPU/1GiB. Both connectors again select
10 short cadences and enter the native account worker twice under fresh WS;
12 terminal frames are dropped, hidden fills keep levels reserved, and exact
original receipts precede restored quotes. Owner/session/PID stay unchanged.
Its measured release-to-quotes interval is6.119s. The two full-service runs are
branch/financial regression checks with export/SQL/observer overhead, not a
controlled latency comparison; five RED scheduler tests establish the removed
wait. The candidate is not deployed on Cloud128. A fresh connected window is
required before claiming any improvement to ordinary quote gaps or probes.

GET-only readback10:22:12UTC remains revision128/control86, fresh/running,
no HALT or status.error; all seven reads return200. At that instant both markets
have two SELLs, with Spot BUY rejected by sizing/allocation checks and perpetual
BUY inventory-limited. This is guarded one-sided activity, not a two-sided pass.

Evidence: `build/quote-gap-followup-20261113/` contains21 sample contexts,
RED/green/native/PostgreSQL checks and candidate build logs.
`build/e2e-offline/20261113-171357-418a48/` retains the wrapper failure;
`build/e2e-offline/20261113-171748-f20bf3/` is the successful deployed-image fault
qualification and `build/e2e-offline/20261113-172030-ad7782/` the successful
candidate. Test containers/networks are removed. No Cloud configuration, policy,
trading command or DB maintenance is changed in this follow-up.

## Revision128 pending-cancel polling deployment

Ready revision `hummingbot-demo-00128-jdt` and the Artifact Registry tag for
`d82cd5c28ae533e9bac195566b9cca5fe9c4c823` both resolve to
`sha256:8ac9faa971346a55fec5c28748ee9f340a978522c78d36ef2615d0d8eb62b51c`.
Creation is09:21:43UTC and Ready09:23:08UTC; traffic remains100% at the final
readback. Compared with127, env values, resources, Cloud probes, command/args
and volume mounts are unchanged. Observation is GET-only; no trading command,
HALT reset, DB maintenance or Cloud setting is changed.

Window09:24:55.257–09:54:56.178UTC (16:24–16:54 local) retains901 samples /
1,802 successful status/liquidity reads. Sampled span1,800.922s and maximum
gap2.555s pass the unchanged v3 observation-quality checks. The same artifact
and session/control86 persist across generations97→98→99→100; the observed
owner revision matches throughout.
There are888 running,3 starting and10 readiness-wait samples, no status.error.
Two sampled `halted=true` values are stale/mismatched `session_transition`
shutdown fences; each clears in the next observation. They remain unknown,
not healthy quote samples. The third profile handoff has unknown observations
without a sampled HALT; no absence between samples is asserted.

The complete exact-window log query contains54 WARNING records:14 loop-stall/
recovery pairs,17 slow-stage warnings and9 child-probe timeout transitions.
There is no ERROR, child restart or missing-quote warning; diagnostic PID14
persists. The separate deployment/startup query09:21:43–09:24:47UTC retains3
earlier probe timeouts, one loop-stall/recovery pair and one slow-stage warning,
with no ERROR/restart; they are not added to the exact30-minute count. Nine
versus127's13 is a descriptive comparison, not a controlled causal speedup.
The300ms deadline and missing supervisor-recovery INFO limitation remain.

Packets09:29:53–09:53:01UTC show completed counters advancing by1,380 strategy
ticks,3,526 controller cycles,175 submissions,114 cancellations and174 terminal
callbacks, with zero counted errors in these scopes. These are native callback
completions, not venue ACK counts. The narrower diagnostic range proves
progress across retained packets, not uninterrupted ticks or comparable
throughput. Sampled loop stacks include4 commits,3 owner reads,3 startup-
performance locations, one other SQL and3 other Python locations; they do not
uniquely attribute every timeout.

| Market | Both working | Eligible gap | Unknown | Guard excluded | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot |676|14|7|204|676/697 =96.99%, fail |
| Perpetual |0|0|7|894|No confirmed eligible sample; not qualified |

Unknown counts as failure and guards never count as uptime. Perpetual BUY is
inventory-limited in880 samples. Spot's14 eligible-gap samples form12 clusters;
the largest neighboring-observation bracket is6.005s. Its sampled no-quote
timer peaks at5.442s during `guard_recovering`; perpetual's timer peaks at6.041s
in the same guard interval. These sampled timers/brackets are not hard bounds
on unobserved or unknown periods. Failed gaps and unknown samples remain in
the gate.

At09:37:59.530UTC one sample shows one Spot SELL pending cancel while both sides
still work; its neighboring observations span3.983s and show no pending cancel.
There is no per-order wait-age diagnosis in this sample. This establishes
native cancel activity without a repeated sampled98s outage, not that the new
5s fallback was exercised or that every cancel completed within5s. The original
four orders' unique cause remains unproved; missing-terminal-WS behavior is
covered by the local fault tests, not injected into this running Demo.

Final readback09:55:21UTC is fresh/running, no HALT/pending mutation/error,
with2 Spot BUY/1 SELL and2 perpetual SELL; all seven reads return200. The
observer has finished and Demo continues. Evidence:
`build/pending-poll-postdeploy-20261113/`. Deployment/normal operation are
verified; residual probe delays, ordinary quote gaps and the99% target remain
open. No full production or missing-WS connected acceptance is claimed.

## Pending-cancel polling follow-up — local TDD

Code inspection and valid RED tests reproduce two scheduling defects in the
shared native connector. A successful managed cancel ACK sets `PENDING_CANCEL`,
but healthy unrelated WS traffic leaves status polling at120s; failed terminal
proof can likewise stay pending until another event or poll. Separately, the
account loop replaces its wake event after awaiting I/O, losing requests that
arrive during that read. Eight RED cases cover the two OKX connectors. An
additional intermediate RED catches loss of the existing failed-poll retry
when moving event consumption; that retry/backoff is explicitly retained.

The local patch selects the existing5s interval only for live managed orders
awaiting cancel confirmation or terminal proof. Ordinary open/unmanaged/done
orders retain the prior healthy-stream cadence. The same native account worker
consumes its wake before I/O, coalesces in-flight requests into one serial
follow-up, and retries read failures using the original0.5s backoff. There is
no independent financial writer, new worker, env setting or API contract.
This is a scheduling interval, not a5s end-to-end completion guarantee during
slow venue/account reads. Native balances/fill/status polling can be more
frequent while pending, using existing request throttling and serialization.

Shipping native regression passes3,654 +16 subtests, including the new shared
polling cases in the canonical target. Focused connector/tracker/position-poll
checks pass56. PostgreSQL related
recovery/terminal regression passes50; the final five-scenario financial check
passes separately after stronger final assertions. Scopes overlap. The real
recorder/tracker proof covers zero-fill cancel, partial cancel, filled order,
missing fill and explicit51603: ACK alone stays pending, incomplete evidence
cannot terminate the native order, actual amounts/original fees are committed
before successful termination, and the same physical owner survives. There
is only the original submission POST; no replacement is sent. Completed
orders leave the accelerated cadence. Hooks pass for the runtime/test changes.

These tests establish the code defects and protect the fix; they do not prove
the unique cause of the four historical waits. Direct venue GETs were not
completed: local Demo credentials are absent and the read-only DB path to
managed credentials returns OperationalError. No DB/Cloud/venue mutation or
tracker reset is used to obtain evidence. The98.189s observed gap below remains
failed, as does the unchanged quote target. At this stage the patch was local;
revision128's subsequent connected observation is recorded above. Evidence:
`build/pending-cancel-investigation-20261113/`.

GET-only readback09:10:42UTC remains revision127/control86, fresh/running,
no HALT/pending mutation/error; all seven administrative/health reads return200.
At that readback it still ran the prior image, not this fix.

## Revision127 terminal-read and startup deployment

Ready revision `hummingbot-demo-00127-7mv` and the Artifact Registry tag for
`ae553598a95b3cc36741f3808d8b354eb6c153c1` both resolve to
`sha256:7e6078e2f46d2bc8cebf637504e1a6f11286b19df8bbeca13f0613a01e72d1d1`.
Creation is06:45:44UTC and Ready06:47:19UTC. Traffic remains100% at the final
readback. Revision126/127 env values, resource limits, startup/liveness probes,
command/args and volume mounts compare equal. This review uses GET and Cloud
metadata/log reads only; no trading command, reset, Cloud config or financial
DB mutation is issued.

The full observation08:24:36.268–08:54:37.319UTC (15:24–15:54 local) retains901
samples /1,802 successful status/liquidity reads. Sampled span1,801.051s and
maximum gap2.269s pass the unchanged v3 observation-quality checks. The same
revision/session/control86 persists across generations91→92→93→94. There are
880 running,3 starting and18 readiness-wait samples, no status.error. Three raw
`halted=true` samples are stale/mismatched `session_transition` fences during
profile shutdown; each clears in the next sample. They remain unknown/failing
observations, not quote uptime. There is no persistent recovery stop or manual
HALT reset.

The exact-window log query completes with67 records:20 loop-stall/recovery
pairs,13 child-probe timeout transitions,12 slow-stage warnings and2 missing-quote
warnings. No ERROR or child restart; diagnostic engine PID14 throughout. Probe
timeouts remain deadline-censored300ms waits. Thirteen versus19 is31.6% fewer
observed transitions in equal-length windows, not a controlled causal speedup:
market/inventory, work counts, observer cadence and diagnostic coverage differ.
Recovery INFO is still absent from the retained sink, so individual supervisor
recovery durations are not reconstructed from missing records.

Diagnostic packets08:26:42–08:53:01UTC show completed counters advancing by1,568
strategy ticks,3,920 controller cycles,171 submissions,141 cancellations,172
terminal callbacks and33 checkpoints, with zero counted errors in these scopes.
Submission spans are native callback completions, not a count of exchange ACKs.
The packet range is narrower than the observation; counter deltas establish
progress across it, not uninterrupted ticks or normalized throughput. Sampled
loop-stack locations include8 other SQL waits,3 owner reads,3 commits, one
startup-performance accumulation and5 other Python locations. They do not
uniquely attribute every probe episode; synchronous owner/write waits remain.

The quote classifier is copied byte-for-byte from the existing v3 gate. Guards
are excluded, unknowns count as failures, and no risk setting is loosened:

| Market | Both working | Eligible gap | Unknown | Guard excluded | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| Spot |602|58|8|233|602/668 =90.12%, fail |
| Perpetual |0|0|8|893|No confirmed eligible sample; not qualified |

Perpetual BUY is inventory-limited in870 samples. Spot includes one46-sample
eligible-gap cluster08:50:15.190–08:51:45.206UTC (94.056s between neighboring
observations). At08:51:45 the observed no-quote timer reaches98.189s; both sides
show2 pending cancellations and `cancel_confirmation` age91.293s. This is a
real availability failure despite fresh running status, not a guard exclusion.
Four bounded recorded-order timelines then show2 BUY completions and2 SELL
cancellations08:51:47–08:51:48 around the next profile transition. Fresh quotes
return afterward; the final readback08:55:29 is fresh/running, no HALT or pending
mutation, with2 Spot BUY/2 SELL and2 perpetual SELL. Recorded timestamps do not
prove the exchange's original execution/cancellation times.

The shared connector's `_get_poll_interval` can select120s while the user stream
still appears fresh. This is a plausible missed-event fallback to reproduce,
not a uniquely established cause of these four waits; no venue-level trace or
receipt-worker queue timing was captured. Next work is targeted TDD for missing
terminal WS evidence with an otherwise live stream and bounded pending-cancel
reconciliation, retaining full venue/fill/fee/owner proof before releasing an
order or reservation. Do not force-clear the tracker or widen the probe budget.

The separate deployment-to-observer query06:45:44–08:24:22UTC retains6 ERROR
entries: private-WS handshake HTTP502 at08:05:10–08:05:12,08:11:50 and08:21:50–
08:21:52UTC, each logged with a5s retry. The endpoint/intermediary returns502;
these are not key-rejection or DB errors. Current operation is healthy, but no
missing INFO receipt is invented to establish each exact reconnect duration.
This earlier interval also contains41 probe timeout transitions and must not
be mislabeled an ERROR-free deployment merely because the exact30-minute
window has no ERROR.

Evidence: `build/terminal-read-postdeploy-20261113/` contains deployment/config
comparison, private full samples, unchanged classifier/hash, exact-window and
pre-window logs, diagnostic counter/stack summaries, quote-gap contexts and
the four recorded timelines. The local observer has finished; Demo continues
running. Probe elimination, pending-cancel availability and the99% quote target
remain open; this is not full production acceptance.

## Revision126 thirty-minute control and local loop-fairness follow-up

The unchanged image/session/control86 runs for the observer's30-minute lifetime:
351 samples05:54:06.199–06:24:02.382UTC, sampled span1,796.183s, maximum gap5.421s,
all702 status/liquidity reads200. Generations76→77→78→79 advance normally.
Three samples retain `halted=true` with stale/mismatched `session_transition`
observations;8 samples wait for readiness. No persistent stop or sampled error.
Spot is observed two-sided214 times; perpetual remains inventory-constrained.
These are five-second operational summaries, not the canonical quote-window
classifier's full inputs or a99% quote pass; guards/unknowns are not discarded.

The exact bounded log query completes with84 records:24 loop-stall/recovery
pairs,19 child-probe timeout transitions,14 slow-stage warnings and3 submission
guard warnings. No ERROR or child restart; engine PID14 throughout. Between
diagnostic packets05:54:12 and06:23:31UTC, completion counters advance by1,751
strategy ticks,4,554 controller cycles,234 order submissions,135 cancellations,
234 terminal callbacks and63 checkpoints. This establishes continuing progress,
not uninterrupted per-probe ticks. Packet maxima are process-lifetime values,
not necessarily maxima of this30-minute window.

Sampled stack locations include9 other SQL waits,8 owner-fence reads,3 send
attempt writes, one terminal recorder,2 startup-performance accumulations and
one other Python location. A previously retained packet samples `durable_fills`
on the loop. The348.483ms terminal span's detailed children have left the ring;
no unique retrospective decomposition is asserted.

Local TDD separately reproduces two concrete mechanisms. Terminal receipt reads
can keep the loop from serving its probe; read isolation uses one bounded worker
with a worker-owned pooled connection. Owner access and native financial writes
stay on the loop. Immutable proof snapshots cross threads; owner, recorder,
private state and current native fill/fee coverage are revalidated after awaits.
The three-slot REST proof bound remains. Caller cancellation, repeated shutdown
and loop-wide child-Task cancellation drain the shielded executor Future before
releasing slots or lifecycle tracking. The reader has no mutation path.

A real PostgreSQL400ms SELECT blocks a synchronous control's unchanged300ms
HTTP probe. The candidate returns200 while heartbeat callbacks progress, retaining
the same physical owner backend and unchanged position/intent state. This is a
scoped fault-injection result, not proof that all main-loop SQL is eliminated.

Startup now aggregates projected rows in plain per-controller state before
publishing report fields. It retains row order, Decimal arithmetic, finite-value
validation, close-type counts and POSITION_HOLD exclusion; malformed history
does not publish a partial prefix and the iterator still closes. Seven warmed
alternating CPU-component pairs against the exact committed control give
20,000 rows64.087→48.892ms median, one row0.008→0.011ms; outputs match exactly.

Shipping native regression passes3,638 +16 subtests, related PostgreSQL196,
final selected terminal/projection16 and focused fairness/startup14. These scopes
overlap. An earlier selected run fails an admission fixture before reaching the
changed terminal path; isolated and final selected reruns pass. Its original
failure remains in evidence without a uniquely proven retrospective predicate,
guard relaxation or hidden test exclusion. Hooks pass. Evidence:
`build/probe-terminal-investigation-20261113-1253/`.

At this local-qualification stage the candidate had not been measured on Cloud
Run: GitHub denied push for the current SSH identity. The user subsequently
pushed/deployed it; revision127's connected result is recorded above.
No Cloud setting, trading command, recovery/reset or
financial DB mutation is issued in this review. Existing synchronous owner and
write waits remain possible; compare the deployed candidate before claiming
probe improvement or closing the unchanged quote target.

## Revision126 terminal-write and checkpoint-batch deployment

Ready revision `hummingbot-demo-00126-lh2` and the exact Artifact Registry tag
for commit `492649baf2cc6679c5acd62d79a282b1a43c08c6` both resolve to
`sha256:1c6c88e05673ee5bba258eb68e6ec005f75e6acb0047249abd36e0ae4112b232`.
It contains `fb58c2711` and the batching follow-up. Creation is05:36:04UTC,
Ready05:37:34UTC, traffic100%. This review uses GET/API and Cloud metadata/log
reads only; no trading command, recovery/reset, Cloud setting or DB write.

The complete bounded log query is05:36:04–05:45:05UTC (12:36–12:45 local).
API observation05:39:56–05:45:03 retains61 samples /122 successful HTTP reads,
same control/ack86 and session `da0f6de4-face-4fdd-8dab-70181ffb6715`.
Generation74→75 at05:41:46 changes `follow_market` to `follow_token`. One
05:41:48 sample has `halted=true`, stale/mismatched `session_transition`
observation and unavailable quote evidence during cleanup. It is retained as
unknown, not deleted or counted as a quote pass. The next sample waits for
readiness; running returns05:41:59 and2 Spot BUY/2 SELL plus2 perpetual SELL
are observed05:42:04. No manual reset or new Start is used.

Spot BUY and SELL fills are recorded after Ready and after the handoff. The
05:45:03 sample is running, fresh/matching, no active incident/error, pending
terminal0, with2 Spot BUY and2 perpetual SELL. Spot SELL currently reports
`order_size_rejected`; perpetual BUY remains `inventory_limit`. These constrained
periods do not qualify as uninterrupted two-sided quotes.

Logs contain no ERROR, child restart schedule, shutdown timeout or terminal-proof
incident; diagnostic engine PID remains14. There are8 loop-stall/recovery pairs,
9 slow-stage warnings and8 child-probe timeout transitions (300.949–302.780ms,
deadline-censored). Recovery INFO records are absent from this query; distinct
first-failure transitions and continued healthy operation support recovery
between episodes. One early valuation warning has `book_not_fresh` age10.834s;
one submission warning is `spread_policy_quote_rejected`.

New diagnostics show13 completed `position.checkpoint` spans /0 errors through
05:44:54, maximum220.422ms, and native `db.commit` maximum152.117ms /0 observed
errors. Other recorded spans include owner read214.440ms, terminal348.483ms and
successful shutdown total2.380s. Async wall spans include awaits; these values
do not independently attribute the complete probe delay. Checkpoint span data
does not include executor count, so this review cannot prove how many live calls
used the multi-executor branch or establish a normalized Cloud SQL/probe speedup.

At05:43:56 four administrative GETs return401 with fixed `token_expired`
diagnoses. These are expired Google credentials, separate from engine health;
the authenticated observation process continues receiving200. No credential is
changed. The observer is stopped locally; Demo trading continues. Evidence:
`build/checkpoint-batch-postdeploy-20261113-1239/assessment.json`, diagnostic
counters and bounded raw logs/observations. The unchanged30-minute99% quote gate
and remaining probe investigation stay open.

Earlier 2026-11-13: revision124/`64e78147d` is verified at100% traffic. Two
scheduled profile handoffs complete on the new image without observed HALT,
terminal-proof error or engine restart. Seven short probe timeouts recover;
quote constraints remain. This is bounded deploy/handoff verification, not a
passed30-minute quote acceptance. See [revision124 evidence](#revision124-connected-spot-terminal-handoff-follow-up).

## Revision124 connected Spot terminal handoff follow-up

Global build `b42af073-2436-463d-8b65-269c1db7b9cd` succeeds for exact commit
`64e78147d5e1bdfc5e8a9851d0f8be92b0bdf1af`. Its Artifact Registry commit tag
and Ready revision `hummingbot-demo-00124-2cl` both resolve to
`sha256:6c4b65f97a655f81bc1df4d247f82fe067410868080a4b7c7da1041139ddeded`.
Ready occurs04:39:55UTC; service traffic is100% to this revision. The preceding
revision123 is excluded from this qualification despite becoming Ready before
the new build finishes.

GET-only observation04:39:57–04:53:52UTC (11:39:57–11:53:52 local) captures164
new-revision samples, all328 HTTP reads successful, no sampled HALT or service
error. One observation is stale or session-mismatched during transition and is
retained. Six phase samples are stopping/starting/readiness waits, not normal
running. Control/ack86 and session `da0f6de4-face-4fdd-8dab-70181ffb6715` remain
unchanged across generations68→69→70 at04:41:46 and04:51:46UTC. The first
handoff is near startup; the second follows a full600-second profile interval.
Twelve recorded Spot BUY fills are observed after Ready; six fetched BUY order
details are completed with exact filled quantity and zero remaining quantity.
These establish activity before handoff, not direct instrumentation of which
proof branch executes.

At the second handoff, generation70 is preparing at04:51:49, readiness-waiting
at04:51:54, running at04:51:59 and has2 Spot BUY/2 SELL plus2 perpetual SELL
quotes at04:52:05. Pending terminal orders return to0. The final04:53:52 sample
is running with fresh matching observation, no active incident, pending0,
2 Spot BUY/1 SELL and2 perpetual SELL. Perpetual BUY is inventory-limited; Spot
BUY temporarily reports size/allocation rejection around04:53:16 before quotes
return. Those constrained/unknown periods are not promoted into successful
two-sided samples.

Complete bounded Cloud Logging query04:38:35–04:53:06UTC returns29 selected
records:7 loop-stall/recovery pairs,7 slow-stage warnings,7 child-probe timeouts
of300.607–300.907ms, and1 `spread_policy_quote_rejected` submission warning.
There are no ERROR records, child restart schedules, shutdown timeouts,
`spot_terminal_proof_wait` or service incidents. Diagnostic records retain
engine PID14. The first handoff's recorded shutdown total is1.881s; the second
has a1.651s successful `shutdown.proof_before` span. Async stage wall durations
include awaits and do not establish an equally long event-loop block.

The demonstrated complete-commit failure class now has connected handoff
evidence. Revision122's original suppressed predicate remains unknown; future
partial/canceled/uncertain failures still require their own proof and diagnosis.
No Cloud setting, DB value, HALT, credential, trading command or risk limit is
changed during this review. The local observer is stopped; the Demo bot remains
running. Evidence: `build/spot-terminal-postdeploy-20261113/assessment.json` and
its bounded raw observations/logs. The unchanged30-minute99% quote gate remains
open, and this healthy interval does not exercise the recovery API.

### Local probe SQL investigation after revision124

The retained packets have seven sampled loop stalls, with heartbeat gaps at
capture of255.986–281.693ms. Six stacks are inside synchronous PostgreSQL waits:
send-attempt execution/COMMIT, terminal recording/COMMIT and write fencing during
profile cleanup. The remaining stack is compiling an import during startup.
Two send-attempt samples directly overlap child-probe timeouts. Other probe
episodes are not uniquely attributable from the rate-limited samples. The300ms
probe timeout censors its reported duration; it is not the complete stall time.
The `/live` handler itself performs no SQL, but runs on the same engine loop.

PostgreSQL TDD reproduces an avoidable terminal-recording burst: independent
attempt/history lookups autoflush pending changes, producing four guarded
flushes. Coalescing those two reads under `no_autoflush` reduces this to two
flushes and removes two synchronous SQL round trips (15→13 statements in the
paired cancellation fixture). The checkpoint flush and final commit retain
their existing fresh physical-owner checks. Uncertain attempts stay uncertain;
duplicates do not add history; losing the physical owner after the first flush
or failing final COMMIT rolls back order/attempt/status/tracking together.

Cursor SQL instrumentation does not include the COMMIT reply wait. A separate
bounded `db.commit` span now wraps the native `DeadlineConnection.commit`,
preserving its return/exception and all I/O deadlines. It records no SQL,
parameters, credentials or exception text. Actual deferred-constraint COMMIT
success/failure and telemetry failure are tested independently.

Local qualification: five terminal-status RED cases and two missing-COMMIT-span
RED cases fail before their respective corrections. Related regression passes
107 native recorder/tracker tests +5 subtests,45 diagnostic/deadline/lifecycle
tests,182 PostgreSQL recorder/owner/fill/unknown-COMMIT tests and18 PostgreSQL
diagnostic/COMMIT/I/O tests. Scopes overlap. The final affected rerun passes13
tests. Source hooks and whitespace checks pass.

Five final alternating pairs per condition retain15→13 statements. Without an
injected wait, component median22.879ms becomes18.805ms; with5ms added after
every statement,147.923ms becomes134.971ms. Injected latency and local component
timings are scoped measurements, not a Cloud Run speedup or a probe guarantee.
There are no wall-clock performance assertions. The confirmed SQL saving is two
statements per callback in this fixture.

This correction and instrumentation remain local. They reduce a demonstrated
blocking path; they do not remove synchronous PostgreSQL from the engine or
prove that all future probes will fit300ms. The import/send-path waits and the
unchanged30-minute quote gate remain distinct follow-ups. No probe threshold,
Cloud state, spending/risk policy or transaction authority is changed.
Local RED/GREEN and paired evidence: `build/probe-sql-followup-20261113/`.

Earlier 2026-11-13: revision122/`5d540d4e2` is verified at100% traffic. Its
10:31:48 local profile handoff fails Spot terminal proof and restarts the engine
once. At10:38:47 the same session/control86 is running again without HALT; this
healthy snapshot does not erase the restart. The incident and local follow-up
are [recorded below](#revision122-spot-terminal-proof-during-profile-handoff).
This is not a passed30-minute quote/restart acceptance. Revision121's maintenance
recovery remains historical evidence, not a newly exercised recovery API.

## Revision122 Spot terminal proof during profile handoff

Revision `hummingbot-demo-00122-qzw` is created03:23:00UTC and becomes Ready at
03:24:43UTC on13 November (local10:23/10:24). Exact commit
`5d540d4e2d5cd76722d4eb386b9a66ac3460926c` resolves in Artifact Registry to the
same deployed digest `sha256:14e94680d7cc0a2a6328693ea651181ba0573a8169285e74bfaebcae0b398f5f`.
Global build `780e6994-4e7b-490c-b1aa-921e07a44ad5` succeeds. Service receives100%
traffic with2CPU/1GiB. No Cloud setting, command, DB financial/risk value or HALT
is mutated during the review. The new recovery API is deployed but not invoked
against a healthy running session.

At03:31:48.109UTC, terminal proof raises
`spot_outflow terminal fill reconciliation is incomplete`. Shutdown logs two
errors, the service records `service_internal_failure`, and the supervisor
schedules engine exit-code1 recovery at03:31:48.997UTC. Diagnostics confirm the
engine process changes from14 to34 within the same Cloud instance/revision. The
same control86/session is retained. The failure coincides with the scheduled
600s Random profile advance at03:31:46; the profile changes from`follow_token`
to`follow_market`. Source flow advances the session then calls native shutdown;
its terminal hook requires all pending Spot proofs before checkpoint completion.

The bounded log window03:23:00–03:37:17UTC has45 selected records, no pagination:
8 loop-stall/recovery pairs,6 slow-stage warnings,4 child-probe failures (three
300.49–300.89ms timeouts and one503 during shutdown),11 terminal-proof incident
warnings,3 shutdown/service ERROR records, one child restart and4 submission
ERROR records for two later BUY denials with `spot_outflow_limit`. The latter
are risk-guard refusals, not evidence of an exchange create. Native diagnostic
wall durations include `service.observe`1.486s and `controller.cycle`4.190s; async
wall duration is not proof of a4.190s event-loop block.

At03:33:24UTC the rebuilt runtime reports pending terminal orders0 and resumes
Spot BUY/SELL. At03:38:47UTC all health/API reads return200, observations are
fresh and no HALT/error is active. Both markets have2 working SELLs. Spot BUY
proposal capacity is constrained by1.9086USDT remaining under its500USDT/hour
policy; perpetual BUY retains `inventory_limit`. The earlier failed history-limit
recovery receipt remains old history. Neither current health nor existing fills
qualifies the unchanged30-minute quote target.

Read-only follow-up queries the dedicated Demo DB and native exchange without
mutating either. Four incident-period BUYs have matching identity/size/price and
stable terminal detail: two canceled/zero-fill, two fully filled with one trade
each. Later native replies are complete. The original per-order failure was
suppressed, so its exact cause cannot be reconstructed from these later reads.
Local TDD reproduces a related concrete availability defect: a fully committed
native execution still fails handoff when a redundant REST lookup is unavailable.
The local fix uses exact native-fill/committed-receipt proof for that case,
keeping all financial charges, account/owner/recorder fences and REST verification
for uncertain/partial/canceled cases. New fixed stage diagnostics make subsequent
failures attributable. Local RED/GREEN covers complete committed fills with unavailable REST through
fenced native shutdown, missing/failed commit, lost owner, exact native/fee
identity, partial/conflicting quantities and bounded sanitized stage diagnostics.
Broader native shipping regression passes3,623 tests +16 subtests; PostgreSQL
regression passes110 tests. Final affected reruns pass78 native/service and19
Spot PostgreSQL contracts. These runs overlap. This local fix is not deployed or
connected-qualified by this review. The temporary read-only proxy is closed; no secret is printed or
committed. Evidence: `build/cloudrun-check-20261113-1027/` and
`build/spot-terminal-handoff-20261113/`.

Earlier: revision117/`5c36bb7bd` has a completed901-sample baseline. Observation
quality passes, but the unchanged99% target fails: Spot98.36%, perpetual97.58%.
No manual reset, ERROR, engine restart or8s shutdown timeout occurs in this
window; temporary shutdown fences remain failing unknown samples. Local proof
sharing, executor-identity and freshness-fixture changes are separately qualified,
not deployed. See [baseline results](#revision117-thirty-minute-baseline-during-local-follow-up).
Revision112's completed verification and the revision111 record below remain
historical, with their original failed99% quote gates and scope limits intact.

## Revision121 verified maintenance recovery and restart

Revision `hummingbot-demo-00121-z6s` runs commit
`e635f3da74e751fd0ef0d16f77a87657ec2c332e`; its exact commit tag resolves to
`sha256:9e2b8860e54a42d92cd862dc96d7eb85ea19bd26d002ba8abbe1f850dfc207f6`,
matching the Ready revision with100% traffic. It contains the readiness-wait fix.
The old technical HALT remains until the explicitly delegated recovery below.

Stop completes at control85. Recovery Check receipt
`b066f6c6-6453-45b0-97b1-80f5ad6298f6` is genuinely blocked with
`recovery_scope_limit`: the ledger contains21,860 orders and3,697 fills. No
force field or API limit change is used. The last API check remains a historical
failed receipt; it is not a newly active HALT after maintenance.

Independent read-only verification anchors650 immutable fills and position costs
to audit183 and its original proof hashes. Two complete authenticated native
scans verify the3,047 newer fills (Spot1,202; perpetual1,845), exact identities,
quantities, prices and fees. No exchange order remains open, perpetual exposure
matches, and Spot cash matches the audited prefix plus verified trades/fees.
There are no pending intents, nonterminal attempts or unapplied journal entries;
the current financial/binding hashes match the verified runtime checkpoint.

Replaying individual fills by event timestamp is not the native application
order. Exact replay uses2,969 executor applications plus9 direct cleanup fills
across2 audited disconnects. All financial fields of all3 positions then match
exactly. Local event-clock and venue execution timestamps are retained separately;
no timestamp, order, fill or position is rewritten to make the proof pass.

After fresh venue revalidation, the stopped writer is quiesced using temporary
service scaling. Under the physical advisory fence and version85 CAS, preview
and apply remove only `halt`, `halt_kind`, `reason`, adding audit264 at
17:10:22.152017UTC on12 November (00:10 local on13 November). All financial rows,
credentials and risk checkpoints remain unchanged. Original AUTOMATIC min/max1
scaling, service template and traffic are restored, with no new revision/image.
This is reviewed delegated maintenance after independent proof, not successful
self-service recovery or a claim that the API scope limit has been solved.

The normal admin API Start receipt `0894bdcb-54ad-4ab2-bf0c-c9afedca1beb` succeeds
at17:11:46.686274UTC on12 November, control86, session
`da0f6de4-face-4fdd-8dab-70181ffb6715`. It preserves both markets, Random and
until-stopped duration, and binds the deployed catalogue hash. Maintenance audit
uses an operator-delegated actor; control receipts use the enabled Basic admin.

Thirteen GET observations over about61 seconds remain running with no HALT. The
final17:13:25UTC check has2 Spot BUY and2 perpetual SELL orders. Spot SELL reports
`order_size_rejected`; perpetual BUY reports `inventory_limit`. The bounded log
query from Start through that check returns6 WARNING records, one probe timeout,
zero ERROR/restart/shutdown-timeout events. No full30-minute quote gate, new
load/soak, Google login or GCP KMS acceptance is claimed. Incremental reviewed
recovery beyond the lifetime-history limit remains an open operational item.
Evidence/receipts: `build/readiness-recovery-20261112/`.

## Revision120 connector readiness outage

Revision `hummingbot-demo-00120-l7h` runs commit
`847f38e1ec21c3ab6a7b0808548065dc36ec59a6`, digest
`sha256:da552642ceea0d1fc3f593a7dcbebe1dad9c7706b1fb6ae8204ba653ed4e3ab0`.
The commit tag resolves to the same registry digest. It remains the latest Ready
revision and receives100% traffic; no later deploy explains the incident.

The20:15 local check has three running samples, no HALT and no active service
incident. Spot is two-sided; perpetual BUY waits at its inventory cap while
SELL reduces LONG. Two probe timeouts are retained. This brief check is not
30-minute quote acceptance.

From13:28:52.931UTC (20:28:52 local), both private and public OKX Demo WS
handshakes fail with HTTP502. There are39 such ERROR records, followed by a
startup reconciliation `TimeoutError` at13:29:22.614UTC and a failed-start
summary. The stack waits in `BotOwnership.reconcile` for `connector.ready`;
that passive wait currently consumes the30s exchange-reconciliation deadline.
The exception handler calls `require_recovery`. This establishes the readiness
failure path, not the ultimate infrastructure origin of the502 responses and
not a causal link to the previously corrected absent-leg race.

At16:32:19–16:32:29UTC (23:32 local), all three API samples remain
`halted / RecoveryRequired`, on the same session/control84, generation80.
Current working-order observations are unavailable, not confirmed empty.
The complete bounded WARNING/ERROR query since revision creation returns81
records:40 WARNING and41 ERROR, below the1,000 limit. It includes six probe
failures, zero child-restart events and zero shutdown-timeout records. A later
funding observation OSError is separately retained and does not explain the
20:29 startup failure. No operator Stop/Start, recovery check/reset, exchange
mutation, DB repair, cap or credential change was issued by this investigation.

The local correction moves passive connector readiness outside the ordinary
30s reconciliation deadline, matching the existing cold-recovery readiness wait.
Native connector tasks keep reconnecting; admission stays disabled while waiting.
Ownership is rechecked. Stop/Switch can cancel the task. The30s deadline still
bounds normal open-order reconciliation/cancellation after readiness; its
conservative failure handling and unknown-order/intent/financial checks remain.
A pre-existing HALT is never erased. No env or API contract change is required.
This does not generalize retries to arbitrary post-readiness exchange failures.

Three RED cases reproduce the original deadline failure. The five added native
cases cover delayed recovery, actual service Stop/health, prior HALT, and bounded
read/cancel reconciliation. Three added PostgreSQL cases prove durable-state
preservation across readiness recovery/Stop/prior HALT. Tests shorten the30s
deadline while letting readiness remain blocked for multiple deadline budgets;
they are deterministic lifecycle checks, not network latency benchmarks.
Final native regression passes3,567 tests plus16 subtests; the42 focused tests
are included. Disposable PostgreSQL ownership/reconciliation passes67 tests,
including the earlier9-case selection. Hooks pass. No new packaged-image E2E,
full load/soak, per-fill exchange reconciliation or connected quote gate is
claimed by these lifecycle checks.

Evidence: `build/cloudrun-status-20261112-2332/` and
`build/readiness-outage-fix-20261112/`. The local fix is not deployed and does not
clear the HALT already written by revision120. Exchange/ledger verification and
reviewed recovery remain necessary before restarting that session. The original
99% quote target remains open; no new full observation window is claimed.

## Revision119 connected window and per-leg absence proof

Revision `hummingbot-demo-00119-hd7` is Ready with100% traffic. Its registry tag
is commit `709700c7061d02dece8539522e07d362b8988c1f`, resolved digest
`sha256:5ef986daff314fcc0214d3e658d50633fea646cbb0e369885524fca647a251a1`.
Diagnostics are enabled. This deploys the position-poll scheduling correction;
it does not contain the subsequent local absence-proof correction below.

Measurement12:05:27.497461–12:35:28.135187UTC (19:05–19:35 local) retains901
samples on the same revision, session and control84 across generations71–74.
Wall span1,800.638s, maximum gap3.762s, zero API errors. The original classifier
hash `ce60b823674db68a850cafd03fe7e90d77dc8cd0484716bee31b315e6a03e753`
and its unknown/guard rules are unchanged. Quality passes; the target fails.

| Market | Two-sided | Eligible gap | Unknown | Guard excluded | Conservative result |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spot |345|30|10|516|345/385=89.61%, fail |
| Perpetual |360|41|10|490|360/411=87.59%, fail |

Only375/901 Spot and401/901 perpetual samples are confirmed eligible. Inventory
skew suppresses Spot BUY sizing in many samples; the early perpetual LONG is
0.0499BTC against the unchanged0.05BTC cap. Size, cap, allocation and readiness
restrictions are retained separately. These different market/inventory/profile
conditions do not form a controlled speed comparison with revision118. Even
removing ordinary replacement gaps would leave375/385 and401/411 because the
unknowns still count as failures; both paths need attention to meet99%.

All three scheduled profile changes finish without restart. Six raw HALT
samples and two owner-unavailable samples remain in the original classification.
Forty-two samples wait for readiness. `reduction_position_unavailable` occurs at
12:12:25.165535 and12:12:51.197463UTC, then clears; it is not relabeled as fixed.
A bounded, two-page recorder read finds146 perpetual fills:75 CLOSE and71 OPEN,
no pagination truncation. All have subsequent samples; two lack a complete15s
follow-up before window end. Twenty overlapping post-fill15s windows contain
one of the two unavailable samples, not twenty distinct incidents. This is
recorded history plus observations, not fresh per-trade OKX REST reconciliation.

Exact-window logs contain146 WARNING records below the1,000-query limit:
49 supervisor probe-timeout transitions,31 diagnostic stall/recovery pairs and
25 slow-stage packets, zero ERROR, child restart or shutdown-timeout events.
Fourteen probes have a sampled stall within2s; rate-limited packets do not
explain every probe. Sampled maxima include controller7.01ms, tick341.47ms,
reports382.83ms, SQL217.22ms and guarded submission1.397s. These are sampled
spans, not complete operation percentiles. Client-read status p99/max is
1.133/2.114s; liquidity0.933/1.873s. Neither is exchange ACK latency.

Observed shutdown totals are1.520s,8.040s and2.421s. The second core span is
7.947s; proof-before2.933s and proof-after4.874s explain most of it. The total
span also includes work before the8s core deadline is established, so8.040s
does not itself imply a logged deadline violation. The core has very little
margin; retain this concern and the historical timeout investigation. At
12:36:47UTC the API is running, error/HALT/pending false, both markets two-sided.

Code inspection plus RED tests find another availability coupling in the custom
WS-versus-REST fencing, introduced before this release: any leg's WS version
change discards the entire pair response. With fresh LONG WS updates, that can
discard otherwise valid full-REST proof that SHORT remains absent. Earlier race
coverage protected LONG from overwrite but did not assert fresh absent-peer
admission. This mechanism is reproduced; the exact failed predicate was not
captured in the two live unavailable samples, so unique causality is unproved.

The local correction retains the existing whole-pair fence and does not advance
the full-pair snapshot on a raced response. Separate in-memory absence timestamps
require a complete successful CROSS/HEDGE REST response, exact instrument,
valid unique sides and finite values, and an unchanged WS version for that
specific absent leg. A WS update to that leg invalidates its proof; net/unknown
side events invalidate both. A version change also invalidates old position
metadata before WS parsing, retaining quantity but blocking admission if parsing
fails or is canceled. Positive REST presence clears old absence proof.
Absent-leg admission cannot fall back to a fresh pair timestamp. A recent pair
timestamp also cannot suppress a refresh when required leg evidence is invalid;
the controller wakes the existing native reader without doing REST itself. The
regular full-snapshot polling cadence and admission age limit are unchanged. All native REST
position callers share a lock, preventing out-of-order responses from renewing
old absence evidence. No native position quantity is partially reapplied.

Local qualification:21 new native cases,3,562 canonical tests plus16 subtests,
and both packaged LONG/SHORT cases pass. The final amd64 image
`sha256:d380cd6f94330d8c1f5f8dda628f292179404f5e8819be172cb9b71239dfc49c`
matches8 relevant runtime sources; manifest
`sha256:28fdb1934486ffc7081f79c76f0e802ac81a9f82fb9c137d28c6c013bca01ce8`.
LONG/SHORT receipts are `build/e2e-offline/20261112-195606-e37e27/` and
`build/e2e-offline/20261112-195606-0938bc/`, including partial application once,
lost ACK, fees, maker replacement and cold restart. No new full soak, real
Google/GCP KMS or per-fill native reconciliation is claimed. No operator Cloud
configuration, trading command, DB repair, credential, cap, pricing or
freshness-threshold mutation was issued during this observation.

Evidence: `build/revision119-quote-qualification-20261112/`, including the raw
samples, unchanged analysis, logs/correlations, recorded-fill follow-up and local
source identities. Deploy and measure the local correction separately. Keep
ordinary replacement gaps, profile-handoff unknowns and proof deadline margin
open; this correction does not establish continuous99% quoting.

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
