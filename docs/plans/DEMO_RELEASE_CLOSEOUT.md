# Demo and release closeout

Scope approved 2026-11-10: complete controlled OKX Demo qualification and the
latest packaged release. Real GCP KMS, the actual frontend and live funds remain
separate. Use existing audited operator APIs; never manufacture fills, disable
guards or rewrite financial state to obtain a pass.

- [x] Reconcile older checklists with their original image/evidence scopes.
- [x] Fresh read-only Cloud preflight: revision66, Demo, managed/local-encrypted
  credentials, running19/19, no HALT. Spot BUY is inventory-skew constrained;
  perpetual gross exposure leaves insufficient room for another entry.
- [x] Baseline canonical native passes2,991 +16 subtests. Initial PostgreSQL
  attempt has600 passes, one outdated projection assertion and one migration
  fixture setup error without retained SQLSTATE. Correct the assertion, retain
  the original failure and repeat the full suite; targeted four cases pass.
- [ ] Complete final canonical native and disposable PostgreSQL regressions.
- [ ] Pin/download the deployed image and compare its packaged source to the
  reviewed working tree. Retain immutable image/config/fixture hashes.
- [ ] Stop through the operator API; back up the stopped Demo database.
- [ ] Disconnect the perpetual slot through its audited cleanup: cancel managed
  orders, close managed hedge legs, verify fills/fees/flat exposure, then remove
  only that saved bundle. Reconfigure the same account; remain stopped.
- [ ] Build a public-config-only Demo overlay for the pinned runtime. Keep
  allocation100 USDT/market, drawdown100/controller and200/global, all exposure
  caps, post-only, maker-fee floor, freshness and ownership controls. For this
  temporary test, use a declared spot inventory target near observed cash weight
  and an explicit interpolation band; this is not wallet rebalancing or a
  production recommendation. Record exact settings and a rollback artifact.
- [ ] Deploy the overlay to the same single Cloud Run service, verify metadata,
  credentials, readiness and catalogue hash, then issue a separate audited Start.
- [ ] Observe a natural full perpetual entry fill. Match trade ID, base quantity,
  price and original fees to REST. Prove POSITION_HOLD handoff, preserved exposure,
  no duplicate application, safe release of that level and a fresh account check
  before a replacement. A guard may correctly prohibit replacement.
- [ ] Measure30 minutes of both-sided quoting. Report eligible denominator,
  acknowledged BUY/SELL numerator, explicit policy/readiness exclusions and
  unknown observations separately. Zero eligible samples is unqualified. Missing
  quotes during ordinary replacement still count against the99% eligible target;
  stale data never proves working orders. Retain20s/60s absence diagnostics.
- [x] Diagnose batch-refresh continuity gap on revision67; source Red7/control1,
  native lifecycle Green106, then a separate invalid-peer Red. Implement opt-in
  bounded rolling refresh without delaying safety withdrawal or trusting a
  pending/invalid peer. Final regression and packaged qualification remain open.
- [ ] Run all packaged E2E groups, a distinct-image upgrade/rollback scenario,
  unchanged warmup/mixed/burst/60-minute soak/fault/retention budgets, and financial
  reconciliation on the pinned candidate. Simulated load fills remain separate
  from actual Demo fills. Preserve failures and rerun only affected gates after
  fixes; a changed runtime requires fresh artifact qualification.
- [ ] Restore the regular Demo inventory/spread catalogue with its explicit
  five-second rolling-refresh limit after temporary qualification, verify
  restart/reconciliation and leave the approved Demo trading state explicit.
- [ ] Publish a compact acceptance matrix with artifact IDs, results, failures,
  residual limitations and the actual audit principal; close only proven boxes.

The earlier real Google credential/recovery lifecycle is already qualified.
This technical qualification can use the enabled bootstrap administrator and
must retain that actual principal in audit, rather than claim another Google
session. No Google token or exchange secret belongs in reports or command output.
