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
  All9 profile/mode cases pass. Independent terminal reads confirm404 canceled
  post-only orders with exact original price/base quantity and both sides in
  every case. One failed local intent has no exchange order. New fills/fees are
  zero, so positive new-fill fee processing remains unobserved on this image.
- [ ] Leave trading stopped with no test-owned pending quotes, preserve private
  files/DB/backup, audit logs, publish sanitized scoped evidence and commit.

Raw helper/results: ignored `build/okx-demo-current/`. The public evidence must
contain no API keys, passphrases, DB URL or admin password.
