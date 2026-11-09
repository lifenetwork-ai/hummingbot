# Plans and remaining verification

Updated 2026-11-10. Start with the [current image result](../../reports/release/mm-runtime-followup-load.md)
and [operating guides](../README.md). Detailed phase checklists, rejected attempts
and original image/date scopes remain in [Git history](../../reports/README.md#historical-records).
Removing old files does not close unresolved evidence or policy items.

Latest connected acceptance is Cloud revision65 (`c98b08a6f…`): Google recovery
checks431 orders/101 fills, applies one reviewed spot repair, preserves protected
state and remains stopped before a separate Start. The subsequent30-minute,
360-sample observation has no HALT/child replacement;103 fills match REST,
including two new natural spot fills. Perpetual is inventory-cap blocked, so this
is not two-sided-liquidity or perpetual full-fill qualification. See the
[completed recovery plan](ADMIN_RECOVERY_API_TDD_PLAN.md) and
[Cloud evidence](../../reports/platform/cloudrun-demo-abrupt-restart.md#revision65--google-recovery-and-separate-start).

The earlier Random-cleanup failure on `b090430c…` and separately reviewed
bounded pass on `4451974e…` retain their original scopes. The last full offline
release remains `b090430c…`; it does not qualify the newest runtime image.

## Last full local qualification (`b090430c…`)

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
- [ ] Freeze/pin the latest corrected runtime candidate and run its full release
  gate: complete native/PostgreSQL/packaged E2E, load,60-minute soak, fault and retention
  checks before production closeout. The revision65 targeted/source/30-minute
  Demo results do not transfer the older full gate to that image.
- [x] Cleanup reporting source correction: real-core Red1/control-pass1; native96
  and PostgreSQL109 pass. Keep cleanup strategy-free and retain recorder config
  identity; genuine unknown strategies still fail. The historical3.682-second
  warning stays recorded. That older image's packaging/cleanup evidence and the
  latest revision65 recovery observation remain distinct; the newest full gate
  is still open above. [Demo scope](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Remaining connected market and product qualification

- [ ] Observe perpetual full-fill handoff on a deployed corrected image: exact
  fill/fee/exposure accounting, safe level release and fresh post-fill account
  checks. Current exposure caps prevent a new perpetual entry; no override is
  authorized merely to obtain a passing test.
- [ ] Qualify the approved two-sided quote target in a30-minute window with
  eligible BUY and SELL samples. Revision65 has359 policy-constrained samples
  plus one readiness-guard wait, so its denominator is zero and the target is
  unqualified. Unknown/stale observations must not count as working orders.
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

- [Admin recovery — completed source and Cloud workflow](ADMIN_RECOVERY_API_TDD_PLAN.md)
- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance are separate from local gates.
Procedures are in [Cloud Run operations](../cloudrun.md) and [FE integration](../fe-integration.md).
