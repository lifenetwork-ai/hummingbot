# OKX Demo and current offline image acceptance

Latest deployment2026-11-15:152/`caab218c1` verifies bounded fill-read batching
at100% traffic with matching immutable artifact, unchanged spec/session/control90
and policy/peaks. Recorded activity continues;28 checkpoint completions have
zero errors in the retained diagnostic snapshot. Keep two recovered probes,
the6.82 s controller span and one transient readiness503; health returns200.
No application ERROR/restart/ownership loss is observed. The30-minute quote and
residual-probe gates remain open; see [verification](../../reports/performance/mm-quote-replacement-latency.md#revision152-deployment-verification).

Earlier deployment2026-11-15,12:11 ICT:151/`4b302019a` verifies the recovery-label
fix, same session/control90 and risk peaks/policy, matching financial/binding
hashes and all health200. Short smoke and49 recorded orders/14 Spot fills pass;
four probes recover. See [deployment verification](../../reports/demo/cloudrun-drawdown-2500-5000.md#revision151-recovery-label-deployment-verification).

Completed2026-11-15,11:22 ICT: source150/`3d59c383f` preserves the approved
2,500/controller–5,000/global limits and peaks. Its30-minute window has901
samples, no failed API reads,222 recorded orders/99 Spot fills and final matching
financial/binding hashes; all health200, same owner/control90 and no operator-
required HALT/restart/ownership loss. Three Random shutdown fences auto-clear;
nine unknowns remain counted. Spot95.43% fails99%; Perpetual has no eligible
two-sided sample. Five probes recover, with three earlier probes separate.
INFO retention and quote/probe acceptance remain open. The separate recovery
label correction is deployed by151. See [the scoped results](../../reports/demo/cloudrun-drawdown-2500-5000.md#revision150-source-deployment-and-connected-follow-up).

Earlier read-only risk review2026-11-15,10:07 ICT: native covered-report drawdown
is Spot1,477.60/limit1,000, Perpetual398.57/1,000 and global1,323.52/2,000 USDT.
Venue quantities/pending orders and audited Spot cash changes match. Native
latch-only rearm replay stops again; live risk settings/latch remain unchanged.
This is current revaluation, not account equity loss or09:12 trigger-time PnL.
See [risk review](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision147-drawdown-revaluation-and-unchanged-policy-replay).

Latest deployment check2026-11-15,09:40 ICT:147/`f3567186c` is verified at100%
traffic with unchanged settings. The bot inherits a Spot drawdown stop recorded
on146 at09:12:45, before the new revision Ready09:19. DB financial/binding hashes
match with no unresolved work; startup/live200, ready503 and no post-Ready
orders/fills. The1,000/2,000 policy and risk latch are unchanged. No quote window
is started while halted; INFO retention remains blocked. See
[revision147](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision147-deployment-with-inherited-drawdown-stop).

Local follow-up after146 repairs missing submission/probe evidence; final native,
PostgreSQL and same-image packaged checks pass. The enabled flow retains168 phase
records/zero drops; final finite logs have no ERROR/probe transition, with the
initial losses/probes preserved. The code is deployed from147. Cloud INFO retention
remains blocked by403 for `logging.sinks.update` / `logging.exclusions.update`;
the sink is unchanged. No quote/probe/perpetual gate is closed. See
[the local scope](../../reports/performance/mm-quote-replacement-latency.md#revision146-retained-evidence-and-local-follow-up--2026-11-15).

Latest 2026-11-15,08:36 ICT: revision146/`3e0392607` is verified at100% traffic,
unchanged2 CPU/1 GiB/env. The901-sample30-minute window passes collection quality,
all status/liquidity reads return200 and final health/checkpoint hashes match.
There are272 recorded orders/129 Spot fills, no final pending/uncertain/unapplied
work, ERROR/restart/ownership loss or incident HALT reset. Six probe warnings
remain, with three earlier startup probes retained separately. Spot95.06% fails99%;
Perpetual has no confirmed eligible two-sided samples. Five unknowns include one
temporary shutdown fence that clears next sample. Fast INFO is excluded by the
project sink; header timing is verified but complete server-log correlation and
the original status-timeout cause remain open. No guard or threshold is relaxed.
See [revision146](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision146-status-timing-and-native-pre-ping-observation).

Latest 2026-11-15, 03:14 ICT: revision145/`f5f181e92` is verified at 100% traffic,
unchanged 2 CPU/1 GiB/env. Its completed collection has897 samples, 187 recorded
orders/76 Spot fills and matching checkpoints, but quality fails after one status
timeout. Spot95.74% still fails99%; Perpetual remains unqualified. Four probes
remain; no engine restart/ownership loss. Final process is live/no HALT but both
markets have zero quotes under `reference_deviation`; `/ready`503 is retained
as an HTTP ERROR, distinct from an application exception. No guard is widened.
See [revision145](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision145-single-held-checkpoint-deployment-and-observation).
Separate 03:18:23 ICT readback: generation175 is running/no HALT, health200,
Spot2 BUY/2 SELL and Perpetual0 BUY/2 SELL with unchanged control88/88. The
automatic return to quotes does not close the failed window's gates.

Latest2026-11-15,02:09ICT: revision144/4fa9ef95c is verified at100% traffic,
unchanged2CPU/1GiB/env. The valid repeated30-minute window has901 samples,
230 recorded orders/102 Spot fills, matching checkpoints and final running/
control88/88/health200. Three profile shutdowns complete without ERROR/restart/
ownership loss. One300.798ms probe timeout remains; Spot93.70% fails99% and
perpetual has no confirmed eligible two-sided samples/new fills. The first
Mac-sleep-interrupted attempt remains invalid. This is not full release
acceptance. See [revision144](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision144-guarded-admission-deployment-and-observation).

Local follow-up after revision142 reduces ordinary intent/Created persistence
from four guarded flushes to two and adds bounded timing for these DB stages.
Native929 tests +9 subtests,217 disposable PostgreSQL tests and two same-image
packaged scenarios pass. The final local shutdown run still has one303.954ms
probe timeout, recovered after1.043s without restart/HALT. The fix is now deployed
and included in revision144; its local evidence does not close quote/perpetual or
the residual probe gates. No config/migration is needed. See
[the local follow-up](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision142-intentack-db-follow-up--local).

Latest2026-11-14,23:13ICT: revision142/`f61e6516c` is verified at100% traffic,
unchanged non-image settings. Its complete901-sample30-minute window passes
sample quality, records270 orders/141 Spot fills and ends running/control88/88/
no HALT with health200 and matching checkpoints. Three profile shutdowns finish
at1.440/1.148/1.130s, without observed ERROR/restart/ownership loss. Three short
probe timeouts remain. Spot95.32% still fails99%; perpetual has no new fill or
confirmed eligible two-sided samples. No submission-block event occurs, so the
fixed branch has no positive connected fault receipt. This is not full release
acceptance. See [revision142](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision142-session-fence-deployment-and-observation).

Local follow-up after revision141: the shutdown-fenced maker retry is reproduced
with native connector/PostgreSQL (12 local IDs, no transport). The correction
retires one managed entry without retry, retaining fills/fees and genuine safety
errors. Native/PG and both packaged regressions pass; it is now deployed on revision142 and
requires no new env/migration. See
[the local correction](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision141-session-fence-retry-correction--local).

Latest2026-11-14,20:09ICT: revision141/`d56186f29` is verified at100% traffic,
unchanged non-image settings. The complete30-minute window retains898 samples
with quality pass,186 recorded orders/47 Spot fills and matching checkpoints.
Final health is200; control88/88 is running/no HALT. Three profile shutdowns
finish in1.588/1.404/1.521s without restart/ownership loss. Spot98.06% still
fails99%; perpetual remains unqualified. Five probe timeouts, one delayed
observation and7 guard-block ERROR entries/paired retry logs remain. This is
not an ERROR-free result or full release acceptance. See
[revision141](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision141-shutdown-proof-deployment-and-observation).

The deployed cleanup correction has3,760 native +16 subtests,98 PostgreSQL and
2 packaged E2E passes. Historical local failures and connected limitations stay
retained. See [the local correction](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision140-shutdown-proof-sharing-and-local-correction).

Previously2026-11-14: revision140/`53f21fa38` is independently verified at100% traffic.
Its unchanged901-sample30-minute window has204 recorded orders/65 Spot fills,
no in-window ERROR/restart,8 probe timeouts and matching final checkpoints.
Bot remains running/no HALT/control88/88. Quote acceptance is still open:
Spot97.68% fails99%; perpetual has no confirmed eligible two-sided samples/new
fill. A pre-window8s shutdown timeout/restart remains a separate open incident.
See the [revision140 evidence](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision140-cash-progress-deployment-and-observation).

Earlier2026-11-13: revision121/e635f3da7 deploys the readiness-wait correction.
The old technical HALT is recovered after independent verification, audit264,
with zero financial/risk changes. Start succeeds at control86, both/Random/
until-stopped. The00:13 local short check is running with active orders, no HALT,
no ERROR/restart/shutdown timeout and one probe timeout. Original99% quote
acceptance and recovery beyond the API history limit remain open. See the
[revision121 recovery](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision121-verified-maintenance-recovery-and-restart).

Earlier 2026-11-12: revision117/5c36bb7bd clears the old shutdown fence and
returns to quotes, then recovers quotes after its next scheduled profile change.
This is scoped verification: unavailable/guard samples, original cleanup timing,
the liquidity test failure and the30-minute quote gate remain open. See
[revision117 results](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision117-verified-fence-recovery-and-next-profile-change).

Its subsequent901-sample baseline fails the unchanged99% target
(Spot98.36%, perpetual97.58%) while recovering all three scheduled profile
changes. Local shutdown-proof/identity fixes and freshness-fixture qualification
do not replace new-image connected acceptance; see the
[follow-up](../../reports/performance/mm-quote-replacement-latency.md#shutdown-proof-sharing-freshness-fixtures-and-executor-identity).

Earlier 2026-11-12: revision116 has diagnostics enabled but encountered a
profile-switch shutdown timeout and a retained drained recovery fence. The
initial two-sided samples do not close acceptance; the local successor-branch
correction and its qualification are recorded in the
[revision116 investigation](../../reports/demo/cloudrun-quote-replacement-acceptance.md#revision116-diagnostics-and-drained-shutdown-fence).

Earlier follow-up 2026-11-12: revision112/`1c1fa280a` runs the post-only CLOSE
correction; native readback confirms fresh-price replacements without same-executor
replay. Its completed901-sample window still fails99% (Spot86.84%, perpetual94.74%).
The bot is running without an incident HALT reset/owner restart. Probe causality
remains open; see [connected acceptance](../../reports/demo/cloudrun-quote-replacement-acceptance.md).

Updated 2026-11-12. Revision110's connected close-first results are in the
[current acceptance record](../../reports/demo/cloudrun-close-first-baseline.md):
83 actual maker LONG close fills, exact financial/cost replay and two cold owner
handoffs on continuous Start84. The completed regular Random quote target fails
99% and remains open. Earlier image-specific checklists, approvals, failures, natural
fills and REST proofs remain in the [acceptance history](../../reports/README.md#historical-records)
(Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`). A local pass never changes
the image/account/date attached to connected evidence.

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../../reports/release/demo-release-closeout.md#later-revision-transition-under-investigation).

## Earlier Demo/release closeout —2026-11-10

See [current checklist](DEMO_RELEASE_CLOSEOUT.md) and
[acceptance matrix](../../reports/release/demo-release-closeout.md). Source
`f53885fda` / regular image `4198f2fa…` passes native3,085 +16, PostgreSQL605 and
all30 packaged E2E. Full unchanged load/Random soak3600/six faults/retention pass.
Historical rollout attribution is closed; latest merged CI artifact verification remains open.

- [x] Revision73 qualifies full perpetual handoff for both BUY/SELL and the
  thirty-minute conditional quote target (spot99.7700%/perpetual99.7133%).
  Explicit guard exclusions and zero unknown are reported separately.
- [x] Corrected-image automatic Disconnect/Set and exact stopped384-fill readback;
  regular catalogue cold reload preserves independent flat hedge accounting.
- [x] Restore regular catalogue on revision74 and separately Start53, both/fixed
  follow_market, Until stopped, no HALT. Existing wallet inventory can still
  legitimately restrict BUY; the temporary acceptance settings are not retained.

## Earlier connected acceptance — Cloud revision65 (`c98b08a6f…`)

- [x] Real Google Check/Confirm recovery verifies431 orders/101 fills, repairs only
  the reviewed spot aggregate, preserves financial history/risk/bindings and
  records audit. Duplicate confirmation does not repeat the repair.
- [x] Separate Google Start and30-minute observation:360 fresh runtime/owner
  samples, no read failure/HALT/child replacement, final running19/19. All103
  fills match REST, including two new natural spot fills.
- [x] Those missing market cases are qualified separately on revision73 above;
  revision65 itself remains inventory constrained and supplies no such proof.

[Recovery acceptance](ADMIN_RECOVERY_API_TDD_PLAN.md),
[Cloud scope/evidence](../../reports/platform/cloudrun-demo-abrupt-restart.md#revision65--google-recovery-and-separate-start).
No new full release/soak or real GCP KMS acceptance is claimed here.

## Earlier full image qualification — local/offline (`b090430c…`)

- [x] Build/pin `b090430c…` at `d0791e563`, 1,374,595,999 bytes; 1,225 packaged
  hashes match; 1,254 measured source/fixture hashes remain unchanged.
- [x] Canonical native 2,861 + 16 subtests; paired real-signal Linux-image contract
  confirms the pre-existing Event-lock hazard fixed in `1916e048e`.
- [x] All27 available E2E groups pass in one all execution, including inventory
  shutdown/restart. Standalone distinct-image technical upgrade/takeover/rollback
  passes against963, without qualifying that failed image as production rollback.
- [x] Unchanged full warmup/mixed/burst/Random soak3600/five faults pass: main p99
  **569.69/273.48/297.81/313.00 ms**, burst auxiliary 133.70 ms. All 97,800 main / 24,450
  auxiliary healthy reads HTTP200, zero unexpected stale responses.
- [x] 4,885 exact simulated fills/fees/positions reconcile; retained growth
  **30.33 MiB/64 MiB**, 58 soak samples running with PIDs 1/32/33 unchanged.
- [x] Disclose three recovered healthy engine probes 1.116/1.083/1.057 s; no healthy
  ERROR/replacement. No baseline activity overlay or exact quote-downtime proof.
- [x] Preserve failed963 burst/all attempts in Git `d0791e563`, accepted a6 records
  in `8544488ef`, at current release paths. All existing unrelated container IDs/
  states, image IDs and volume remain; no image prune.

[Current report](../../reports/release/mm-runtime-followup-load.md),
[portable evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
All trading is simulated with dummy credentials. Actual Demo acceptance is
separate. The 27-case all command omits previous-image; its raw upgrade flag is
false, with the separate passing upgrade run retained explicitly.

## Earlier actual Demo acceptance — image-specific scopes

Additional hosted acceptance on2026-11-09: [real Cloud Run Google lifecycle](../../reports/demo/cloudrun-google-credentials-acceptance.md)
passes on `hummingbot-demo-00048-ckn`, source/image tag `4ef36ed61…`, observed
1vCPU/512Mi. All credential/trading mutations use the invited Google admin;
five real fills match REST, both slots endv2 Connected, stopped/flat/pending0.
Seventeen protected quote rejections and three probes remain disclosed. Earlier
images/load scopes and the repeated-quote investigation are not closed by this run.

The [connected evidence index](../../reports/demo/README.md) and
[last documented stopped-Demo restoration](../../reports/performance/mm-admin-worker-lanes.md)
retain historical account/image scope. Earlier inventories preserved the then
existing `086454de…` Demo service and PostgreSQL; that statement does not describe
the present daemon. The original offline release did not query a real OKX
account. The separately requested managed-Demo follow-up below now performs
read-only Demo checks and restores a backup into an isolated database.

The connected follow-up first exercised fixed sessions and three natural spot
fills on `b090430c…`, then failed Random step cleanup. The separately reviewed
recovery and bounded connected replay now pass on `4451974e…`; the failed earlier
run remains failed. [Earlier local connected report](../../reports/demo/managed-local-connected-followup.md). Earlier
historical positive fill/REST evidence and
unchecked controller-image spot/soak/guard-wait items keep their original dates
and images; new offline gates do not retroactively complete them.

- [x] Read-only source-connector preflight on 2026-11-09: both dedicated Demo
  keys authenticate REST/private WS; Futures/Hedge mode, zero pending orders,
  BTC contract size verified. Both keys resolve to the backup's account identity.
- [x] Restore the newest available Nov-04 backup into a separate local database
  and run existing SQL migrations. Preserve 3,513 orders, 23 fills and three
  positions; long/short base quantities and observed BTC cash match the venue.
  The restored bot is stopped/acknowledged, without HALT or pending mutations.
- [x] Prepare and roll back the exact account-binding dry run. The shipping
  `account_binding_required` fence remains intact; no real order was submitted.
  [Reviewable proposal](../../reports/demo/managed-local-cutover-proposal.md).
- [x] Operator approved and applied the exact binding transaction: two metadata
  rows plus one audit, with immutable order/fill/position fingerprints preserved.
- [x] Shipping-image API Set/load and stopped cold restart pass for both slots
  with `postgres_kms`/local KEK. Versions `2` are connected; service has no exchange
  credential environment variables. Stored envelopes contain no plaintext key
  candidates. First restart inspection corrected the client's changed dynamic
  published port; the service itself restarted successfully.
- [x] Nine fixed profile/mode combinations and Stop pass. REST confirms post-only
  pending orders; perpetual BUY is explicitly blocked by `inventory_limit` at the
  preserved long cap. Three new natural spot fills match individual REST and order
  aggregate quantity/price/original fees; all 23 older fills remain unchanged.
- [x] Initial active Random restart resumes the same until-stopped session and
  loads the same two credential versions. Subsequent Random step cleanup fails;
  the full Random/restart acceptance is not passed.
- [x] Bounded terminal-readback source fix `aba5d745a`: native2,868 + 16 subtests,
  PG557, packaged signal/readback proof and targeted Random/lifecycle E2E pass on
  `4451974e…`. All 1,229 packaged files match. New full release/soak not yet run.
- [x] Separately reviewed HALT recovery and connected replay pass on `4451974e…`: 'both'
  Random steps0/1/2/all three profiles, same-session active restart and Stop.
  Disconnect closes managed perpetual exposure, removes only selected envelope;
  API reconfigure/stopped cold restart pass. Old23 fills/3,513 order IDs preserved;
  all ten new fill parts match REST, no duplicates. Bot stopped, no HALT/orders/
  perpetual exposure. One optional reporting warning recovers; see the report.
- [ ] Run full release/soak on the remediated candidate before production closeout.
  See the
  [credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md) and
  [PostgreSQL maintenance rules](../postgres-runtime.md).

Real GCP KMS, production quote-coverage policy and unresolved historical/probe
attribution are in the [remaining-work register](README.md).
