# Current-image OKX Demo acceptance

Operator request: 2026-10-30. Selected runtime `715a11f3…`, after completed local
regression/E2E/load/hour/recovery/control acceptance. Earlier real Demo results
retain their original image scope.

Use the existing dedicated Demo account, PostgreSQL history, controller IDs,
private environment/catalogue and bound policies. Preserve existing long/short
positions. No HALT reset, forced fills, live routing or Cloud Run deployment.
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
- [ ] Verify unique recorder identities through repeated independent readback
  and filled-exposure restart; preserve session/anchors/deadline and reconcile
  held position quantities after Stop.
- [x] Stop/cancel all observation quotes, restore the canonical service/profile
  stopped, preserve fill/executor history, venue positions and private files,
  and publish sanitized evidence.
  Canonical API is up with HALT retained. The failed restart's two stale aggregate
  rows are documented; immutable fill/executor history is preserved. No reset.

No-fill or partial coverage remains explicitly open; the zero-fill result above
does not close this follow-up.

### Filled-restart defect remediation

Original image715a fails this new restart case: managed YAML `initial_positions`
overrides post-fill durable holds, then failed initialization stores stale seed
aggregates. Fill history survives; no pending quotes remain. Canonical API is up
with trading stopped and HALT retained. See [follow-up report](../../reports/okx-demo-natural-fill.md)
and [scoped evidence](../../reports/okx-demo-natural-fill-evidence.json).

- [x] Reproduce stale seed overwrite and failed cleanup using Red tests; verify
  a managed bootstrap-only fix and preserve upstream unmanaged override behavior.
- [x] Independently compare all six exact quantities/prices/original fees, venue
  maker status, spot cash, perpetual exposure and three unique-ledger readbacks.
- [x] Reconstruct two aggregate rows from the pre-test snapshot and matched fills;
  validate conditional repair on a disposable post-failure clone, preserving
  fills and HALT and rejecting stale repair replay. Actual Demo remains untouched.
- [x] Complete broader native/PostgreSQL and replacement-image E2E verification,
  including seeded positions followed by fills and active restart.
  Native2,047 + 11subtests, PostgreSQL268 and targeted image E2E pass. Original
  image fails the same seeded test. Fixed imagee526118b…/manifest566788f2… is built
  from runtime commit783d35c69. This does not claim a new full release/hour gate.
- [ ] Obtain explicit operator review before actual Demo aggregate maintenance
  and clearing only this resolved HALT; no automatic reset or history deletion.
- [ ] Repeat real filled-exposure recovery with the fixed image, independently
  reconcile final quantities/fees/dedup and restore stopped canonical service.
