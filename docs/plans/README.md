# Plans and remaining verification

Updated 2026-11-10. Start with [Demo/release closeout](DEMO_RELEASE_CLOSEOUT.md)
and its [current acceptance matrix](../../reports/release/demo-release-closeout.md).
Detailed rejected attempts retain their original image/date scopes; removing old
files does not close unresolved evidence or policy items.

The [quiet-feed / unsent-quote plan](MM_FEED_QUOTE_RECOVERY_TDD_PLAN.md) tracks the
new targeted offline work; its gates do not replace the full release below.

Last full release baseline `f53885fda` / regular image `4198f2fa…` passes native3,085 +16
subtests, full PostgreSQL605 and all30 packaged E2E groups in one `all` execution,
including distinct-image upgrade/takeover/rollback. Full unchanged load/Random soak3600/six faults/retention pass: p99
96.87/96.46/103.11/283.41ms,4,885 simulated fills, growth18.02MiB/64MiB.
The old full offline result below keeps its own scope.

Actual Demo revision73 qualifies BUY/SELL perpetual fill handoff and the approved
30-minute two-sided quote target: spot99.7700%, perpetual99.7133%, zero unknown,
eligible/all97.70%/97.98%. Temporary sizing/spreads and explicit guard exclusions
remain disclosed. Automatic close/remove/same-account Set needs no manual Retry;
384 stopped actual fills/fees/positions reconcile. Regular catalogue is restored
on revision74; a separate audited Start53 leaves the Demo running Until stopped.
Real KMS, actual FE and live cutover remain separate below.

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../../reports/release/demo-release-closeout.md#later-revision-transition-under-investigation).

## Earlier full local qualification (`b090430c…`)

- [x] Reuse Hummingbot engine/connectors/V2 executors; PostgreSQL/UTC/Decimal,
  ownership/durable-before-send/reconciliation/recovery, fixed/Random sessions,
  spot/perpetual/both, operator APIs, encrypted credentials and risk controls.
- [x] Recorder/quote allocation source work: bounded book reads, disabled DEBUG
  formatting, one report DTO, fresh allocation reuse and first-row anchor fallback.
  Exact-value/failure tests and paired component measurements remain in the
  [source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md).
- [x] Fix older missing dummy perpetual credentials in seed E2E (`556d30bb7`),
  add bounded shutdown evidence (`683773d34`), preserve original failures.
- [x] Confirm/fix pre-existing signal/Event-lock hazard (`1916e048e`), real-signal
  Red1/related Green75, paired old/new Linux-image behavior. Parent checks a request
  flag at existing 50 ms waits/launch boundaries; no new thread or deadline change.
- [x] Build/pin `b090430c…` at `d0791e563`, 1,374,595,999 bytes; 1,225 packaged
  hashes match, 1,254 measured source/fixture hashes frozen. Only two source files
  differ from predecessor `96368970…`; selected installed versions match.
- [x] Canonical native **2,861 passes + 16 subtests**; **27 available E2E groups
  pass in one all execution**, including inventory shutdown/restart.
- [x] Unchanged full warmup/mixed/burst/Random soak3600/five faults pass. Main p99
  **569.69/273.48/297.81/313.00 ms**; burst auxiliary 133.70 ms. Zero unexpected
  healthy errors, 4,885 exact simulated fills/fees/positions, retained growth
  **30.33 MiB/64 MiB**. Three healthy probes recover without child replacement.
- [x] Preserve failed predecessor `96368970…` in Git **`d0791e563`** at current
  release paths: burst p99 1,084.01/1,151.07 ms, corrected `all` shutdown failure,
  then thirteen passing individual targets. Its failures remain failures.
  Earlier accepted `a6fe88c0…` records remain in **`8544488ef`** at the same paths.
- [x] Complete standalone technical upgrade/takeover/rollback check between the
  distinct schema-compatible comparison image `96368970…` and `b090430c…`.
  The comparison image failed its original gate; it is not an approved production
  rollback. The 27-case all command itself did not include `--previous-image`.

## Deferred external acceptance

- [x] Hosted Cloud Run Google identity and Set/Start/Disconnect/Set-again/Stop
  lifecycle pass on revision48 with five REST-matched actual Demo fills. Final
  stopped, both slotsv2 Connected, pending0/perpetual flat. [Cloud result](../../reports/demo/cloudrun-google-credentials-acceptance.md).
- [x] Reproduce/fix the fixed-entry retry mechanism after fresh spread rejection;
  native/PostgreSQL guards and retiring-level tests pass, with bounded revision52
  connected refresh observation. Retain the historical17 rejections/34 ERRORs and
  three probes: their exact original triggers were not forced remotely. See the
  [scoped source/Cloud results](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md#cloud-run-real-google-lifecycle-follow-up--2026-11-09).
- [x] Deployed admin-reviewed HALT recovery and separate Google Start pass on
  revision65, including stopped financial comparison, atomic audit, duplicate
  confirmation and the subsequent360-sample running observation.

- [x] Real Google sign-in, subject binding, admin role, audit and Google-only
  restart pass in the dedicated local auth database. Operator browser result
  after restart is matched to durable login audit; Basic remains rejected.
  [Evidence](../../reports/implementation/google-login-acceptance.json).
- [ ] Real GCP KMS/IAM with synthetic credentials remains deferred;
  local encryption does not close it.
  [Credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
- [x] Operator-reviewed actual-Demo managed-credential cutover, shipping-image
  Set/load/stopped cold restart and nine profile/mode combinations pass. Original
  order/fill history is preserved; three new natural spot fills have individual
  REST/order aggregate proof. Initial active Random restart resumes the same
  session, but the subsequent Random step run fails cleanup and retains HALT.
- [x] Qualify bounded-readback remediation on `4451974e…` with native2,868 +16
  subtests, PG557, package/signal proof and targeted Random/lifecycle E2E. This
  does not transfer the old image's full release/soak results.
- [x] Separately reviewed HALT recovery and connected Random/restart/cleanup pass;
  both hedge sides close, selected key removed/reconfigured and stopped cold
  restart preserves exact ledger. [Connected report](../../reports/demo/managed-local-connected-followup.md).
- [x] Freeze/pin the latest corrected runtime candidate and run its full release
  gate: complete native/PostgreSQL/packaged E2E, load,60-minute soak, fault and retention
  checks before production closeout. The revision65 targeted/source/30-minute
  Demo results do not transfer the older full gate to that image.
- [x] Cleanup reporting source correction: real-core Red1/control-pass1; native96
  and PostgreSQL109 pass. Keep cleanup strategy-free and retain recorder config
  identity; genuine unknown strategies still fail. The historical3.682-second
  warning stays recorded. That older image's packaging/cleanup evidence and the
  revision65 recovery observation remain distinct; the newest full gate
  is qualified separately above. [Demo scope](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Remaining connected market and product qualification

- [x] Observe perpetual full-fill handoff on the corrected image: exact REST
  trade IDs/quantity/original fees, POSITION_HOLD, before-refresh replacement and
  fresh matching account observations. Revision73 has one BUY and one SELL witness.
- [x] Qualify the approved two-sided quote target in a complete30-minute window.
  Revision73:1,780 samples, one owner, no HALT/unknown, spot1,735/1,739 and
  perpetual1,739/1,744. Guard exclusions never count as working orders and ordinary
  gaps remain failures. This does not qualify the restored regular catalogue's
  two-sided uptime for the current wallet.
- [ ] Full FE browser acceptance in the separate frontend deployment. Google
  helper/API/Swagger acceptance does not qualify the actual operator dashboard.
- [ ] Production/live cutover acceptance when the operator chooses that scope;
  current connected trading is Demo, with real GCP KMS still deferred above.

The recorded Cloud migration setting is false. If the operator later enables
guarded auto-migration=true, verify a controlled Cloud rollout with that setting;
its local PG/process contracts do not constitute that enabled Cloud exercise.

## Unresolved policy and historical attribution

- [x] Operator approved initial quote targets on 2026-11-09: at least 99% sampled
  two-sided coverage in eligible 30-minute periods, unexpected absence warning
  after 20 seconds/escalation after 60, with explicit guard/bootstrap/unknown
  classification. This is policy agreement, not a claim that the short local
  diagnostic qualifies 30 minutes or guarantees continuous quoting. The
  [review criteria](../fe-integration.md#reviewing-quote-availability) distinguish
  replacement, guards and stale/unknown evidence without inventing numeric targets.
- [ ] Exclusive historical probe/p99 attribution remains unproven. Current AC
  measurements have fewer probes and lower mixed/burst/soak p99, but higher warmup
  p99 and retained growth. The shutdown-only fix is not a proven cause of healthy
  latency changes; neither power nor code gets exclusive attribution. Earlier
  failed 2.6-second minute/6→14-probe evidence remains in Git `41d290393`.
- [ ] The predecessor's observed nonzero inventory shutdown lacks its original
  Docker State/exit number. A real-signal mechanism is proven fixed, but that does
  not establish the unique cause of the historical event. New State evidence is
  retained before cleanup; the new E2E case passes exit0/non-OOM.
- [ ] Older owner-publication/p99 trigger still lacks contemporaneous host/storage
  proof. Controlled WAL/loop witnesses establish mechanisms, not unique history.
  [Original investigation](../../reports/README.md#historical-records)
  (Git: `docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md`).
- [ ] Historical OKX expiry-response shape lacks its isolated real-venue witness;
  simulated parser/transport cases are separate.
  [Original item](../../reports/README.md#historical-records)
  (Git: `docs/plans/OKX_EXPIRED_SUBMISSION_TDD_PLAN.md`).

Earlier controller-image connected spot/soak/guard-wait boxes keep their original
scope in Git `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`. New local acceptance
never retroactively passes another image or a connected-Demo test. Current and
historical failures, capture omissions and finite-retention limits are in the
[release report](../../reports/release/mm-runtime-followup-load.md).

## Retained plans

- [Automatic MM recovery / availability](MM_AUTOMATIC_RECOVERY_AVAILABILITY_TDD_PLAN.md) — implemented source, offline and connected qualification tracked separately
- [Admin recovery — completed source and Cloud workflow](ADMIN_RECOVERY_API_TDD_PLAN.md)
- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance are separate from local gates.
Procedures are in [Cloud Run operations](../cloudrun.md) and [FE integration](../fe-integration.md).
