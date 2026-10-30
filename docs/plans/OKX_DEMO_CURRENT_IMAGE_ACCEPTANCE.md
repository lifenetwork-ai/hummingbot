# Current-image OKX Demo acceptance

Operator request: 2026-10-30. Selected runtime `715a11f3…`, after completed local
regression/E2E/load/hour/recovery/control acceptance. Earlier real Demo results
retain their original image scope.

Use the existing dedicated Demo account, PostgreSQL history, controller IDs,
private environment/catalogue and bound policies. Preserve existing long/short
positions. The original zero-fill protocol performed no HALT reset, forced fills,
live routing or Cloud Run deployment. The later operator-approved two-row repair
and explicit resolved-HALT clear are documented in the follow-up below.
Keep Start/Switch/Stop on the admin API; YAML remains the policy authority.

- [x] Read-only preflight: dedicated credential validation, same account, Demo
  REST and public/private WS, active target/reference instruments, balances,
  pending orders, positions and durable recovery/command state.
- [x] Validate the current catalogue and ten runtime fingerprints, back up the
  persistent DB, replace the stopped old container with the accepted runtime,
  and prove existing order/fill/position rows are unchanged across startup.
- [x] Spot: exercise `follow_market`, `sideways`, `follow_token` with independent
  exchange order/recorder checks, post-only/rules/budget validation and Stop.
  Each runs through a 65-second observation. Stop leaves zero pending quotes
  and preserves the existing perpetual quantities. Initial order snapshots may
  be partial while levels are being created/refreshed; no full-book count claim.
- [x] Spot Random: observe a real configured 600-second step boundary, restart
  during the session and verify the pinned session/anchors/deadline, then Stop.
  Step0 follows the market; step1 follows AVAX. Session/profile/generation,
  anchors and global/step deadlines survive restart; quote recovery and Stop pass.
- [x] Repeat the three fixed profiles for perpetual and both markets while
  preserving existing exposure and all admission/risk gates.
- [x] Reconcile final pending orders, newly observed fills/original fees and
  position quantities against PostgreSQL. Explicitly report zero-fill coverage
  if the market produces no natural fills; do not invent positive fill evidence.
  All 9 profile/mode cases pass. Independent terminal reads confirm 404 canceled
  post-only orders with exact original price/base quantity and both sides in
  every case. One failed local intent has no exchange order. New fills/fees are
  zero, so positive new-fill fee processing remains unobserved on this image.
- [x] Leave trading stopped with no test-owned pending quotes, preserve private
  files/DB/backup, audit logs, publish sanitized scoped evidence and commit.
  Service remains available with trading stopped, owner healthy, no HALT or
  pending mutation/command. Eight retry ERROR records are classified as guard
  rejections and all eight attempted client IDs are absent on OKX. See the
  [report](../../reports/okx-demo-current-image.md) and
  [sanitized evidence](../../reports/okx-demo-current-image-evidence.json).

Raw helper/results: ignored `build/okx-demo-current/`. The public evidence must
contain no API keys, passphrases, DB URL or admin password.

## Positive natural-fill follow-up — operator request 2026-10-30

The completed zero-fill protocol above retains its scope. This follow-up uses
the same accepted runtime/DB/account and a temporary private Demo catalogue:
40 USDT per market, one level per side, base spread0.0003 before the unchanged
maker-fee floor and NATR multiplier. All guard/exposure/initial-position policies
and controller IDs remain unchanged. Preserve and restore the canonical service
and original catalogue. Maximum observation1200s; fills are never forced.

- [x] Refresh read-only account/WS/DB preflight, compare existing position units,
  validate the temporary catalogue and take a private DB backup.
- [x] Observe at least one new natural maker fill in spot and perpetual; compare
  exchange trade IDs, exact base quantity/price and original fee currency/amount.
  Six natural maker fills match independent OKX reads: one spot and five perpetual.
  Spot fee-net BTC cash and perpetual baseline plus OPEN fills match exactly.
- [x] Verify unique recorder identities through repeated independent readback
  and filled-exposure restart; preserve session/anchors/deadline and reconcile
  held position quantities after Stop.
  Fixed imagee526 passes real active restart and resumes both markets. All six
  original fill rows remain byte-equivalent and unique; no additional fills occur
  during this retry. Exact quantity/cost/fees and venue exposure match after Stop.
- [x] Stop/cancel all observation quotes, restore the canonical service/profile
  stopped, preserve fill/executor history, venue positions and private files,
  and publish sanitized evidence.
  Canonical API now uses fixed imagee526, original catalogue restored, stopped
  without HALT or pending mutations/orders. Private files and all four ledger/
  position hashes are unchanged across the final startup after reviewed repair.

Positive fill and real filled-restart acceptance are complete across the scoped
original observation and fixed-image retry. Neither run forces exchange fills.

### Filled-restart defect remediation

Original image715a fails this new restart case: managed YAML `initial_positions`
overrides post-fill durable holds, then failed initialization stores stale seed
aggregates. Fill history survives. After the approved repair and passing fixed
retry, canonical API is up with trading stopped and no HALT. See [follow-up report](../../reports/okx-demo-natural-fill.md)
and [scoped evidence](../../reports/okx-demo-natural-fill-evidence.json).

- [x] Reproduce stale seed overwrite and failed cleanup using Red tests; verify
  a managed bootstrap-only fix and preserve upstream unmanaged override behavior.
- [x] Independently compare all six exact quantities/prices/original fees, venue
  maker status, spot cash, perpetual exposure and three unique-ledger readbacks.
- [x] Reconstruct two aggregate rows from the pre-test snapshot and matched fills;
  validate conditional repair on a disposable post-failure clone, preserving
  fills and HALT and rejecting stale repair replay before actual maintenance.
- [x] Complete broader native/PostgreSQL and replacement-image E2E verification,
  including seeded positions followed by fills and active restart.
  Native2,047 + 11subtests, PostgreSQL268 and targeted image E2E pass. Original
  image fails the same seeded test. Fixed imagee526118b…/manifest566788f2… is built
  from runtime commit783d35c69. This does not claim a new full release/hour gate.
- [x] Obtain explicit operator review before actual Demo aggregate maintenance
  and clearing only this resolved HALT; no automatic reset or history deletion.
  Operator approved both preset and reviewed repair/retry. Fresh backup/readback,
  stopped writers and conditional two-row update preserve all original history.
- [x] Repeat real filled-exposure recovery with the fixed image, independently
  reconcile final quantities/fees/dedup and restore stopped canonical service.
  Real restart resumes four post-only quotes with pinned session state; exact
  held quantity/cost/fees/volume and three unique-fill reads pass. Final API is up,
  original catalogue restored, stopped/no HALT/no pending quotes or mutations.

## Full release revalidation of fixed imagee526 — operator request 2026-10-30

This is a new acceptance scope, after the completed real Demo follow-up above.
Pin image `sha256:e526118b94324ab4a116839c171482fffa4c3e66adc5effa626d2c0242172454`
and manifest `sha256:566788f22f8c86f1a7df38088bf23557baf7d1c16cff2c4b411ed2f64e4cb918`.
Use isolated fake-exchange/PostgreSQL fixtures; leave persistent Demo stopped and
its order/fill/position history unchanged. Preserve all failures and unchanged
budgets. No live trading, registry publication outside loopback, or cloud deploy.

- [x] Verify immutable image/runtime fingerprints, unchanged budget/runner source,
  host power/lid readiness, stopped Demo baseline and native/PostgreSQL regression.
  Native2,047 + 11subtests and PostgreSQL268 pass. Ten packaged runtime module
  hashes match source. The pinned manifest is cached locally; Demo baseline hashes
  and original stopped intent are recorded for final comparison.
- [x] Complete packaged runtime/ABI/GC/Swagger checks and all seventeen E2E groups,
  including seeded filled restart and compatible image upgrade/rollback.
  All17 pass at `build/e2e-offline/20261030-141530-9aeb28/result.json`, including
  distinct previous-image upgrade/rollback and retained economics.
- [x] Verify ordinary shipping supervisor/API/engine crash/hang recovery and reaping.
  The actual entrypoint passes API repeated crash/hang, engine crash/hang,
  retained HALT, process reaping and ownership release at
  `build/service-supervision/20261030-145208-f3d592/result.json`.
- [ ] Run the unchanged all-feature load gate: warm300, mixed900, burst90,
  continuous soak3600 and all six recovery faults; 2 CPUs/2GiB, pool5 and TTL15.
  First attempt `20261030-145524-a2b39f` fails soak p99: primary1086.76ms,
  auxiliary1137.27ms; zero HTTP errors. Warm/mixed/burst pass. Independent six
  fault cases pass at `20261030-161919-6d9c57`; these do not turn the failed full
  attempt into a pass. Repeat `20261030-162807-3036fa` passes the latency budgets
  but fails zero-error acceptance: one daily reporting query hits PostgreSQL's
  2s statement timeout, causing 15 explicitly stale responses over4.2s. Preserve
  both failures and diagnose without weakening the healthy gate.
- [x] Validate complete per-role minute samples, stable PIDs and bounded
  RSS/thread/descriptor/queue growth, three-profile coverage and exact fill/fee/
  position/funding ledgers. Keep the existing zero-error and p95/p99 budgets.
  The failed first attempt nonetheless passes these specific retention/ledger
  checks: 57 samples, max gap66.35s, stable role PIDs, 4,890 matching fills,
  all three profiles and no retention violation. Its p99 violations remain open.
- [ ] Complete matched ordinary-entrypoint warm300/mixed900 control using the
  passing load run's unchanged fixtures, image and resource limits. A diagnostic
  from the failed baseline also fails mixed p99/backlog; it does not close this item.
- [x] Recheck persistent Demo preservation, clean temporary resources, publish
  scoped portable results, check only achieved items and commit. A failed gate
  remains open until a justified fix and an unchanged repeat pass.

### Operator priority: slow reporting and trading availability

On 2026-10-30 the operator clarified that a slow aggregate is acceptable if the
MM engine is unaffected. Preserve the historical strict zero-degradation failure;
evaluate this operational priority separately, without presenting stale data as
fresh or claiming an absolute independence guarantee for shared CPU/PostgreSQL.

- [x] Inject one actual 2s PostgreSQL statement timeout into the API's daily fee
  read on the isolated shipping image; verify stale observations are clearly
  labelled, retain the original observation time, remain under60s old, suggest a
  bounded retry and later become fresh. No unrelated HTTP/semantic errors.
- [x] Independently observe at least one new fill durably committed to PostgreSQL
  while the reporting query is still blocked; verify exchange/recorder/position
  agreement, continuing market updates/fills, same owner/session/engine PID,
  no HALT and clean Stop. Record the observer's overhead and test limitations.
- [x] Repeat the controlled timeout with a CPU-heavy fee-query expansion on the
  same constrained DB; measure engine progress and report separately from the
  wait-only case. A single synthetic test cannot establish universal isolation.
- [x] Report both the strict release result and the operator-priority evidence,
  including that a server-side wait does not establish isolation from CPU-heavy
  SQL or all future Cloud Run workloads.

### Engine liveness follow-up — 2026-10-30

- [x] Reconstruct the retained restart/lag timeline without treating sampled stacks
  or correlated host activity as a proven root cause. The original restart was
  126.698s after the daily fallback and 9.877s after an engine cancellation callback.
- [x] Add bounded supervisor failure/recovery diagnostics with Red/Green tests:
  distinguish timeout/HTTP status/connection failure, failed-probe count, elapsed
  failure time and sampling gaps. Preserve the 300ms timeout and existing grace.
- [x] Exercise real loopback HTTP503/timeout and isolated source-overlay wait
  diagnostics; retain every failed attempt separately. Overlay runs are not
  acceptance of the original e526 image or a newly packaged release.
- [x] Repeat the CPU-fault diagnostic after the operator reopened the Mac,
  preserving thresholds and all prior failures. Run `20261030-210016-db909f`
  passes: 120 matching fills, 99.98318 simulator updates/s, p99 API 89.70ms, no HALT
  or engine replacement. Two transient probe timeouts recover. The recorded
  clamshell sleep began after the prior tests, so it does not explain their failures.
- [ ] Identify or reproduce the original sustained-failure/long-lag cause with
  sufficient evidence. Short transient timeout recovery does not close this item
  or either full release gate above. See
  [investigation](../../reports/mm-engine-liveness-investigation.md).

### Packaged supervisor-diagnostics release — operator request 2026-10-30

Build source commit `e9a1645b7` into a new immutable image. No source overlays,
no real-exchange actions and no replacement of the stopped persistent Demo.
Preserve historical failures. Report strict release acceptance separately from
the operator's tolerance of slow/explicitly stale aggregate reporting.

- [x] Build the immutable image; verify packaged supervision source hashes and
  capture host power/lid state and Demo preservation baseline. Image `dd602fb2`
  is built; registry manifest pinning is part of E2E preparation.
- [ ] Complete packaged runtime smoke and offline E2E, including prior-image
  upgrade/rollback, on the newly built artifact.
- [ ] Exercise shipping supervisor/API/engine crash/hang recovery on that image.
- [ ] Run warm300/mixed900/burst90/soak3600 and all six fault cases with unchanged
  rates, resource caps, pool5, TTL15 and error/latency/retention budgets. The
  existing `--keep-going` option may retain later windows after budget failures;
  it must still return a failed overall result when any window fails.
- [ ] Run matched ordinary-entrypoint warm300/mixed900 control. If the baseline
  fails, any additional control run is explicitly diagnostic and cannot close
  strict acceptance.
- [ ] Reconcile ledgers, review probe episodes and continuity, retain every
  result, verify Demo preservation, clean temporary resources and commit report.

Progress: [packaged release report](../../reports/probe-release-20261030.md).
