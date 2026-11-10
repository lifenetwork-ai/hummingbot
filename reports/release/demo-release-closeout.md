# Demo and release closeout — 2026-11-10

The controlled Demo qualification passes perpetual fill handoff and the approved
thirty-minute two-sided quote target. The regular catalogue was restored on
revision 74 and separately started at control 53/53, fixed follow_market/both,
Until stopped. A later service update exposes revision75; its exact image/env
await verification. Current API reads are running with the same session/control,
Demo environment and no HALT. **The pinned image passes native, PostgreSQL,
all30 E2E, full unchanged load, Random soak3600, six faults and retention. Overall
Cloud closeout remains open for revision74→75 image/log verification.**

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
| Latest API read | Revision75 at02:58 UTC, running53/53, Demo/both/follow_market/Until stopped, regular catalogue matches, both slots Connected (v2/v9), no HALT, startup/live/ready200. Image/env metadata unverified; this is not inherited image4198 qualification. |

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

Subsequent direct API read exposes `hummingbot-demo-00075-wbf`, while retaining
the same session/control 53. A new revision requires a service update; this is
consistent with a rollout handoff and must be checked before calling it a runtime
defect. No tool in this closeout deployed revision75. Its exact image, config and
rollout actor/time cannot yet be read. Preserve the identity-change samples and
do not silently count them as continued observation of image4198/revision74.

This is actual strategy reinitialization, not proof that only an HTTP probe was
slow. It does not invalidate the preceding revision73 qualification, but regular
operation must not be called uninterrupted. Cloud image/log attribution
is pending: gcloud now requires reauthentication and the browser connector fails
to initialize. No speculative runtime patch or risk reset is made. Request
operator reauthentication, retain the original trace, then reproduce a confirmed
code defect through TDD if the logs identify one.

## Limits still open

- The original I/O delay's unique infrastructure trigger is unproved. SQL reads
  rise near the event, while Cloud Run CPU/memory minute means are modest. This
  does not prove a particular query, network or storage cause; shared Cloud SQL
  resources were not changed.
- The ordinary Recovery API still conservatively blocks legacy spot
  completion-order cost basis as `position_baseline_unproven`. The exact reviewed
  incident repair does not relax that generic proof requirement.
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
ignored `build/acceptance-closeout-20261110/`; secrets never enter this report.
