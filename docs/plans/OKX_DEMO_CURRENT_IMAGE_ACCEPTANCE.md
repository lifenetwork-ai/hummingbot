# OKX Demo and current offline image acceptance

Updated 2026-11-10. Prior image-specific checklists, approvals, failures, natural
fills and REST proofs remain in the [acceptance history](../../reports/README.md#historical-records)
(Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`). A local pass never changes
the image/account/date attached to connected evidence.

Follow-up: Cloud Build/audit attributes revision74→75 to the delayed automatic
deployment of older commit `f7da0d1e6`; no new runtime defect is reproduced.
Overlapping-owner E2E and91 native controls pass. Merged CI artifacts require
separate verification; they do not inherit image4198 release gates. See [closeout investigation](../../reports/release/demo-release-closeout.md#later-revision-transition-under-investigation).

## Latest Demo/release closeout —2026-11-10

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
