# Demo and release closeout — 2026-11-10

Latest incident: revision80 retained an untracked-order startup HALT. After
independent venue/accounting verification and a rehearsed, audited recovery,
separate Start55 succeeds at07:50 UTC and initially places two actual Spot SELL
orders. Later stale Demo reference books withdraw quotes and make readiness
intermittent; the engine remains running without HALT. BUY sizing/inventory and
perpetual exposure caps also restrict new quotes.
No financial data, credentials or risk limits were changed. Details below;
the earlier revision73 quote qualification keeps its original scope.

The controlled Demo qualification passes perpetual fill handoff and the approved
thirty-minute two-sided quote target. The regular catalogue was restored on
revision 74 and separately started at control 53/53, fixed follow_market/both,
Until stopped. Cloud audit now attributes revision74→75 to the automatic Cloud
Build rollout of commit `f7da0d1e6`, not a newly demonstrated runtime defect.
Revision75's older image does not contain the four fixes below; subsequent CI
deployments require their own artifact verification. **The pinned image passes native, PostgreSQL,
all30 E2E, full unchanged load, Random soak3600, six faults and retention. Overall
Cloud closeout remains open for the newly built CI artifact, not historical
rollout attribution.**

## Artifact and acceptance matrix

Source `f53885fdadd29fe430cdd4acbea020e49c65318d`; regular image `4198f2fa…`,
registry digest `sha256:1e7cf901cbe376268d167fde18db0a9f18fa6818ba51052bae9cceba188e2e1b`.
All 1,092 packaged source/SQL/JSON hashes match (1,068 Python); 61 compiled modules
are unchanged from its pinned base. Size 1,375,545,832 bytes. Runtime and host
fixtures are frozen separately. This report does not transfer older image passes.

| Check | Latest result and scope |
| --- | --- |
| Canonical native | 3,085 passed +16 subtests. |
| Disposable PostgreSQL | Full 605 passed. |
| Packaged Linux E2E | All 30 groups pass in one `all` execution, including distinct-image upgrade/takeover/rollback. The older comparison image has known defects and is not an approved production rollback. |
| Cleanup visibility regression | Four injected 51603 reads; exactly two physical closes, five unique fills/fees, flat positions and selected key removed without manual Retry. |
| Full unchanged load | Warmup 300s/mixed 900s/burst 90s/Random soak 3600s pass; main p99 96.87/96.46/103.11/283.41 ms. All 97,800 main and 24,450 auxiliary healthy reads HTTP200; unexpected healthy errors 0. |
| Faults and retention | Six cases pass: feed, volatility defense, owner restart, queued expiry, DB delay and DB failure. Retained growth 18.02 MiB/64 MiB; 58 continuous minute samples, maximum gap 65.50s, supervisor/engine/API PIDs unchanged throughout healthy soak. |
| Simulated accounting | 4,885 unique simulated fills, exact fees/positions, two identified funding bills and three daily snapshots reconcile. These are separate from actual Demo fills. |
| Actual Demo quote target | Revision 73, one owner, 1,780 samples over 1,799.24 sampled seconds; maximum gap 2.80s, no HALT/unknown. Spot 1,735/1,739=99.7700%; perpetual 1,739/1,744=99.7133%. |
| Actual perpetual handoff | Four actual filled post-only orders prove BUY and SELL fill → POSITION_HOLD → replacement before ordinary 60s refresh. Exact REST trade IDs/quantities/prices/original fees and fresh matching account observations. Close timestamps have whole-second precision, not exact round-trip latency. |
| Actual close/remove/Set | Two corrected-image cycles automatically finish without manual Retry. Only the selected perpetual bundle is removed; same-account Set reconnects, remains stopped. |
| Stopped financial readback | 384 actual Demo fills (152 spot/232 perpetual), exact original fees, unique identities, pending orders 0/perpetual flat; BTC cash 0.961563983944. Cold reload preserves distinct flat hedge fee/volume values. |
| Restored operating state | Revision 74, regular 100/100 catalogue, drawdown 100/controller and 200/global, 2 CPU/1 GiB, min 1/max 1, CPU always allocated. Separate audited Start 53 leaves Demo running Until stopped. |
| Historical API read | Revision75 at02:58 UTC, running53/53, Demo/both/follow_market/Until stopped, regular catalogue matches, both slots Connected (v2/v9), no HALT, startup/live/ready200. Its older source/image was later verified from Cloud Build/audit; no image4198 gate is inherited. |
| Post-merge API read | Revision77 at03:48 UTC, same session/control53/53, Demo/running, owner available, no HALT/error, startup/live/ready200. CI deploys merged commit01aab8abe; full new-artifact qualification is separate. |

The qualifying revision 73 overlay `f4863d62…` differs only in public YAML from
the regular runtime. YAML SHA `6982bc84a8b2b668ac0edd7a75b398163c93b1db6ef931cd8e5bc5a3f1734423`:
spot 100/perpetual 40 allocation, allocation ceiling 200, unchanged exposure caps,
drawdown 100/200, guards, post-only and fee floor; declared temporary spot target 0.9
and wider spreads. It is not wallet rebalancing or a production recommendation.
The measured eligible fractions of all samples are 97.70%/97.98%; guards exclude
41/36 samples. Four/five ordinary replacement gaps remain failures. Unknowns count
against the conservative denominator and zero eligible samples cannot pass.
These finite samples do not guarantee uninterrupted quoting or economic profit.

After qualification, the actual packaged regular YAML is verified equal to the
tracked Demo catalogue, including target 0.5 and the explicit 5s rolling limit.
Subsequent Spot BUY inventory/sizing restrictions and both perpetual sides
reaching the gross exposure cap are valid policy blocks;
temporary acceptance settings do not establish the regular catalogue's two-sided
SLO for this wallet. Do not increase caps or weaken guards just to obtain a pass.

Six healthy engine probe timeouts recover in 1.009–1.170s: mixed 2, burst 1,
soak 3, warmup 0. Captured `service.observe_once` spans overlap at 101–1,038 ms;
one context includes GC 14.75 ms, insufficient to explain the whole observation
alone. Bounded traces do not establish a unique cause. Baseline trading-activity
overlay is off, so these intervals are not measured quote downtime. No healthy
soak child replacement is observed. This remains a latency limitation, not a
newly proven code defect or a reason to loosen the existing 300 ms probe budget.

After all gates, selective local cleanup removes 12 unused image identities and
retains 11, including in-use images, the qualified candidates and the old-image
Red/upgrade fixture. Existing container inventory and unrelated images remain;
physical savings are not inferred from summed virtual image sizes.

## Four confirmed defects fixed through TDD

| Defect | Reproduction and correction |
| --- | --- |
| Orphan PostgreSQL session retains ownership after client exit | A real proxy blackholes both directions and retains the upstream session. Red retains the lock beyond 35s. A 30s server idle-session limit permits safe takeover; healthy-owner liveness and idle-reader reconnection have independent controls. Takeover preserves unresolved-effect HALT; no blind unlock. Commit `c1653aa11`. |
| Flat perpetual SHORT checkpoint overwrites LONG accounting | Native and PostgreSQL store/reload/store reproduce both flat summaries becoming BUY. Preserve each flat hedge leg's original side and independent volume, fees and realized PnL. Nonzero direction and spot netting remain unchanged. Commit `6d49d4b8e`. |
| Recovery rejects provable spot fees paid in BTC | Red 2 and related Green 59. Convert original base fees at the exact verified fill price only when equal to the recorded quote fee. Gross quantity remains; unsupported assets/perpetual forms stay blocked. Commit `69fa11070`. |
| Fresh cleanup close is temporarily absent from REST | Actual Demo and immutable old-image Linux Red return 51603 despite real fills. Native Red 5/control 1; poll the same fresh close within its existing 20s deadline, never POST again or accept WS completion alone as REST proof. Other errors retain original handling. Related Green 56, packaged Green and two real automatic cycles pass. Commit `f53885fda`. |

Separate harness corrections fix an observational recovery-check race, a two-read
inventory assertion and excessive read-only observer connections. Their evidence
is retained separately; they are not classified as trading runtime defects.

## Retained failures and reviewed incident recovery

| Attempt | Retained outcome |
| --- | --- |
| Revision 67 baseline | Two-sided target failed: spot 91.84%, perpetual 97.75%. |
| Revision 68 | Interrupted by CI rollout around minute 25; no 30-minute pass. Four actual filled orders separately qualify two handoff witnesses for that controller. |
| Revision 70 | PostgreSQL 3s I/O deadlines cause ownership loss. An idle upstream owner retains the lock after client exit; unresolved effects retain HALT. New ownership and observations are unavailable. |
| Revision 71 first window | Laptop observer proxy connect 5s/handshake EOF interrupts 266s. Cloud owner stays healthy; audited Stop. A read-only contract proves 5 connections/5 reads versus 1/5 with a bounded pre-ping pool. Deadlines unchanged; no samples spliced. |
| Revision 71 complete window | Spot99.3123% and perpetual99.6089% pass failure-inclusive v2 scoring. Original zero-unknown prototype flag remains false; all three unknowns stay failures. Post-window cleanup then fails with 51603; same-operation Retry completes, no repeated physical closes. Old full load is intentionally stopped after warmup/mixed pass; no full soak claimed. |
| Revision 72 on corrected image | 100-USDT perpetual allocation reaches its cap after about 220 eligible samples. Three gaps/one unknown leave this partial window unqualified. Audited Stop, automatic cleanup/Set. A new separate 100/40 window on revision 73 passes above without raising caps. |
| First latest-image full load | Warmup/mixed/burst pass (p99 260.88/288.88/90.99 ms). Clamshell Maintenance Sleep interrupts the soak; no continuous soak/fault/retention pass. Deliberately stopped, separate full unchanged retry started. Cloud status remains running53 without HALT after the laptop gap. |
| Initial successor packaging | Source hash check catches a missing changed development helper. Unqualified artifacts retained; rebuilt before deployment/qualification. |

Under the operator's delegated Demo incident authority, Stop 34 is acknowledged,
service instances are reduced to zero, a backup is retained and five known pending
orders are canceled with independent terminal readback and per-order maintenance
audit. An exact transaction is rehearsed on a disposable restore, with drift
rollback, risk-HALT rejection and repeat-application rejection.

At 22:15 UTC, maintenance audit 107 appends three REST-proven missing fills,
updates eight terminal order projections and repairs three existing aggregate
positions. All 258 original fills, order identities/history, credentials, funding,
risk policy, peaks and latches remain. Only the incident's technical HALT and
quiesced runtime marker are cleared. A stopped anchor plus 68 actual archived
completion witnesses preserves native spot cost basis; chronological replay alone
cannot prove it. Independent readback verifies 261 fills and hedge/cash quantities.
Later close/Set, new windows and final 384-fill proof have separate immutable records.
Technical APIs use actual `basic:bootstrap-admin`; the repair uses maintenance
principal `delegated-demo-closeout-20261110`, not an impersonated Google login or
a separately claimed human confirmation.

## Later revision transition under investigation

At 01:41:01 UTC, durable events change running → stopping → starting → drawdown
report/guard warmup → running at 01:41:14. A fresh minute sample catches
`running=false`, desired running, phase stopping, control 53/53 and the same
session/revision74, owner available, no HALT or error. A later independent read
confirms running again and drawdown below both limits. No operator command was
issued for this transition. The strict monitor aborts and its failed sample is
preserved; a new 5-second incident monitor records any recurrence without mutation.

After CLI reauthentication, Cloud Build `f86c268a-a4e0-4fcc-aca0-9e0800dd435d`
proves an automatic trigger build of `f7da0d1e6ad5987a71d033eb9c805bef48cae4dd`:
created01:33:24 UTC, deploy step01:37:41–01:40:06, SUCCESS. Audit records
`google.cloud.run.v1.Services.ReplaceService` at01:38:02 UTC from
`802449703053-compute@developer.gserviceaccount.com`, using noninteractive gcloud
on Linux. Revision75 is created01:38:03 and becomes healthy01:39:54, before
the observed01:41 ownership handoff. This matches the operator's delayed auto
deploy after push; no new operator Start/Stop was issued. Environment entries,
resources and all revision-spec fields except the image are unchanged from74.
The immutable75 image is `sha256:fadfdd2ce24a9e1614c0863b67441e6d59048d6e71389a28cc85c480e3d9bedb`.
Its source predates the four verified fixes; never transfer image4198's gates to it.

The evidence attributes the revision change/reinitialization to the CI rollout.
INFO/system shutdown records are unavailable under the existing logging filter,
so an exact Cloud SIGTERM timestamp and subphase timings are not independently
proven. This is actual strategy reinitialization, not a probe-only delay or proof
of uninterrupted quotes. There is no new reproduced runtime defect in this event,
and no speculative patch, risk reset or widened guard is applied.

Add explicit overlap coverage to the existing packaged `until-stopped` E2E:
start the successor while the old owner is still running, require healthy standby
startup/readiness503 and unchanged owner/control, then deliver real SIGTERM to the
old process. The successor must acquire exactly the next owner generation,
retain the same session/control/acknowledgement and four unique fills, resume
readiness, and honor a subsequent Stop. This targeted simulator run passes on
immutable image4198; old process exits0 in1.916s, no HALT or unknown exchange
request. Related native lifecycle/signal/standby/supervisor tests pass91.
The added overlap assertions pass the existing runtime on their first run:
they close a coverage gap, not a claimed Red/Green bug fix. Original full-release
evidence remains frozen; this is a separate changed-harness targeted result at
`build/e2e-offline/20261110-104353-22f70e/result.json`, not another full release.

During this read-only investigation, CI also builds merged commits `6fdfcc1b3`
and `01aab8abe`; no manual deployment is made. At03:48 UTC revision77 is running
with the same session/control53/53, available owner, no HALT/error, fresh status
and startup/live/ready200. Its CI source contains the four verified fixes; it is
a different immutable image, so its complete packaged/dependency gate is not
inherited from image4198. The final artifact digest/config and the source build
are recorded separately in the portable evidence.

A separate perpetual private WS disconnect at03:42:46 UTC has close code1006
and enters the connector's existing retry path. Subsequent status and all three
health endpoints are healthy; no control change or HALT is observed. It is not
the01:41 rollout event and is not evidence of a newly reproduced logic defect.

## Real OOM fault diagnostics

Separate real-OOM diagnostics on immutable image4198 pass all three cases with
2 CPU/1 GiB and swap disabled at the fault stage (harness startup uses2 GiB, not
a cold-start capacity qualification at1 GiB). API OOM increments kernel `oom_kill` from0 to1;
API is replaced while engine PID/owner/session remain. Engine OOM after a real
simulator create response also increments0→1; API PID survives, replacement
retains HALT and does not resubmit the uncertain order. Whole-container OOM
targets PID1 while a fill transaction has flushed but not committed: Docker
records exit137/OOMKilled=true, and restart reconciles the one venue fill/fee
exactly once while retaining HALT. Each case verifies exact independent fills
and ends with a bounded clean shutdown; owned containers/networks are removed.

Raw evidence: `build/mm-oom/20261110-115629-0956eb/result.json`. The initial API
attempt reaches real OOM/recovery but fails the ordinary healthy-shutdown gate
because Docker retains its OOMKilled flag; that aggregate failed attempt is kept.
The OOM-specific shutdown check preserves the cause and requires physical exit0;
the ordinary release gate remains unchanged. No runtime defect/fix is claimed.
The mounted test-only wrapper selects its victim with oom_score_adj1000; this
does not establish production victim selection, memory isolation or uninterrupted
quotes in a shared cgroup. These simulator fault diagnostics do not extend the
earlier full load gate or qualify a different CI image.

## Revision79 startup HALT and revision80 recovery

At04:57:07 UTC, revision79 reports an untracked exchange open order during
startup. Revision80 at05:19:22 retains that unresolved-effect HALT. This explains
the stopped engine and quiet trading logs; HTTP startup/live alone do not prove
trading readiness. The original rejected identities are absent from retained
logs, so neither a particular order nor stale venue visibility is a proven
trigger. The preceding local cancellations alone cannot authorize ignoring a
venue pending-order observation.

Independent stopped proof verifies all2,763 recorded orders and453 actual fills
(195 spot/258 perpetual), exact trade identities, quantities, prices and original
fees, zero pending orders, perpetual LONG0.0030/SHORT0.0048 BTC and BTC cash
0.937077583944. All financial fields of the three position summaries match native
completion-order replay: the original103-fill spot anchor,117 archived executor
completion witnesses, the earlier reviewed audit107 transaction and the384-fill
flat perpetual anchor. No position repair is needed.

Audited Stop54 is acknowledged. Standard Recovery Check
`24f28394-5b40-4c11-aa12-5517ad7da5bd` conservatively fails with
`position_baseline_unproven`; this failed receipt remains. Under the existing
delegated Demo incident authority, service scaling is temporarily reduced to
zero, the stopped database is backed up and an exact transaction is rehearsed
on a disposable restore. Financial drift, active ownership, control drift, a
risk-stop latch and repeat application all reject without writes. Production
maintenance audit172 removes only the technical `halt` and `reason` keys;
all financial rows, credentials, risk state, history and stopped intent remain.
This is a reviewed technical repair, not a generic proof bypass or a newly
claimed per-step human approval.

Exact repair plan SHA256:
`29ecc9258db9c11badf1975ca6f286de57fc00c1717c6fd8f9208a77d5b01b1d`.
Quiesced backup SHA256:
`c2b306567b046dc7e22bab7992319d39ff6542bab6f30e3b10d55e171af1aa07`.
Scaling is restored to min1/max1 without changing revision80, its image, env or
resources. Separate audited Start55 uses actual `basic:bootstrap-admin`, fixed
follow_market/both/Until stopped. Fresh REST proves two live Spot SELL orders
matching both ledger and restored tracking identities. Spot BUY remains sized
to zero by the existing inventory policy; perpetual BUY/SELL remain blocked by
the existing gross exposure cap. These policy exclusions are not a new HALT or
a two-sided quote pass.

The strict confirmation then aborts on its tenth sample at07:52:39 UTC because
`/ready` returns503; startup/live remain200 and status is fresh/running55/55,
without HALT. Later status identifies `stale_reference:okx:ETH-USDT`, sometimes
BTC, guard recovery and unavailable drawdown valuation. Quotes are withdrawn
under those conditions. Preserve this failed window; it is not a completed
healthy confirmation or continuous-quoting pass.

A separate unauthenticated45-second Demo public WS capture receives21 BTC book
messages with a20.714s maximum gap, but only two ETH book messages with a16.749s
gap and27.644s age at capture end. These exceed the configured10-second freshness
limit. This corroborates sparse venue updates, rather than proving the exact
delivery history of the Cloud socket. Existing stale-basket withdrawal tests
pass2. The guard and age threshold remain unchanged. Two separate300ms engine
probe timeouts during startup also remain recorded; their unique cause and
exact quote downtime are not proved by these observations.

A separate19-sample follow-up at07:55:59–07:59:06 UTC keeps fresh running55/55,
startup/live200 and no HALT throughout the sampled reads. Readiness is200 in
five samples and503 in14; the final sample is ready200 with Spot SELL quotes
working. This demonstrates automatic guard recovery on fresh data, not a
continuous healthy or two-sided quote window.

Five targeted PostgreSQL regressions pass, including two new cases proving that
a later empty pending-order list does not erase HALT and a terminal local row
does not permit ignoring an untracked venue order. They pass the existing
runtime on their first run; no fabricated Red/Green correction is claimed.
Private evidence is retained under `build/cloud-halt-20261110/`.

## Limits still open

- The original I/O delay's unique infrastructure trigger is unproved. SQL reads
  rise near the event, while Cloud Run CPU/memory minute means are modest. This
  does not prove a particular query, network or storage cause; shared Cloud SQL
  resources were not changed.
- The ordinary Recovery API still conservatively blocks legacy spot
  completion-order cost basis as `position_baseline_unproven`. The exact reviewed
  incident repair does not relax that generic proof requirement.
- Revision80's technical HALT is recovered, but stale Demo reference books
  still cause readiness/quoting interruptions. The failed confirmation remains;
  this recovery does not qualify uninterrupted trading or the latest CI artifact.
- Temporary quote settings, eligibility and different market periods prevent
  exclusive attribution of coverage/latency changes to rolling refresh or the
  cleanup correction. No claim of code being bug-free or of profitable trading.
- Offline load application limits are 2 CPU/2 GiB with amd64 emulation on this Mac;
  Cloud Demo uses 2 CPU/1 GiB. Offline soak does not establish Cloud memory capacity.
- Real GCP KMS/IAM, actual FE browser acceptance and live cutover are outside this
  approved scope. INFO collection remains unavailable to the current identity
  (`logging.sinks.update` absent); no IAM escalation was attempted.

[Execution checklist](../../docs/plans/DEMO_RELEASE_CLOSEOUT.md) ·
[Portable evidence](demo-release-closeout-evidence.json).
Private backups, exact ledgers, hashes and failed/passing traces remain under
ignored `build/acceptance-closeout-20261110/` and `build/cloud-halt-20261110/`;
secrets never enter this report.
