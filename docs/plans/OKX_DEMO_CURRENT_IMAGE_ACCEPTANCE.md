# OKX Demo and current offline image acceptance

Updated 2026-11-09. Prior image-specific checklists, approvals, failures, natural
fills and REST proofs remain in the [acceptance history](../../reports/README.md#historical-records)
(Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`). A local pass never changes
the image/account/date attached to connected evidence.

## Last full image qualification — local/offline (`b090430c…`)

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

## Actual Demo acceptance

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
run remains failed. [Latest connected report](../../reports/demo/managed-local-connected-followup.md). Earlier
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
