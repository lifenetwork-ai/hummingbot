# Demo and release closeout

Scope approved 2026-11-10: complete controlled OKX Demo qualification and the
latest packaged release. Real GCP KMS, the actual frontend and live funds remain
separate. Use existing audited operator APIs; never manufacture fills, disable
guards or rewrite financial state to obtain a pass.

Latest pinned source is `f53885fda`, regular image `4198f2fa…`, digest
`sha256:1e7cf901cbe376268d167fde18db0a9f18fa6818ba51052bae9cceba188e2e1b`.
All1,092 packaged hashes match (1,068 Python),61 compiled modules are unchanged
and2,056 measured source/fixture hashes remain frozen. Canonical native3,085 +16,
PostgreSQL605 and all30 packaged E2E groups pass. Full unchanged warmup/mixed/
burst/Random soak3600 and six fault cases pass; retained growth18.02MiB/64MiB,
4,885 simulated fills/fees/positions exact. Six healthy probes recover without
healthy replacement; no exact quote-downtime or unique latency-cause claim.

Actual revision73 (temporary spot100/perpetual40 overlay `f4863d62…`) completes
1,780 samples across30 minutes, one owner, no HALT or unknown. Spot1,735/1,739
(99.7700%) and perpetual1,739/1,744 (99.7133%) pass; eligible/all97.70%/97.98%.
Four actual filled post-only orders prove BUY/SELL perpetual handoff/replacement
before60s and fresh matching account observations. Stopped384 actual fills,
original fees, pending0/flat perpetual and BTC cash0.961563983944 reconcile.
Two corrected-image close/remove/Set cycles need no manual Retry.

The regular catalogue is restored on revision74 and a separate Start53 leaves
Until stopped/both/follow_market. A later service update exposes revision75 with
the same session/control, Demo environment and regular catalogue, both slots
Connected, no HALT; final startup/live/ready200. Its exact image/environment and
01:41 UTC handoff cause await gcloud reauthentication, so overall Cloud closeout
stays open. Preserve the qualified revision73 window and failed/interrupted
attempts separately in the [acceptance matrix](../../reports/release/demo-release-closeout.md).

- [x] Reconcile older checklists with their original image/evidence scopes.
- [x] Fresh read-only Cloud preflight: revision66, Demo, managed/local-encrypted
  credentials, running19/19, no HALT. Spot BUY is inventory-skew constrained;
  perpetual gross exposure leaves insufficient room for another entry.
- [x] Baseline canonical native passes2,991 +16 subtests. Initial PostgreSQL
  attempt has600 passes, one outdated projection assertion and one migration
  fixture setup error without retained SQLSTATE. Correct the assertion, retain
  the original failure and repeat the full suite; targeted four cases pass.
- [x] Complete preceding candidate native and disposable PostgreSQL regressions:
  that candidate passes3,014 native tests +16 subtests and602 PostgreSQL tests.
  Retain the earlier fixture failures separately; they are not passed attempts.
- [x] Pin/download the deployed image and compare its packaged source to the
  reviewed working tree. Regular candidate `3f6c17db…` / digest `5c6ba758…`
  matches1,092 packaged source files;61 compiled modules match its pinned base.
  Six development-only files are deliberately not packaged. Record host runner
  `0729444fe` separately from shipping source `d09d78871` and freeze fixture hashes.
- [x] Stop through the operator API; back up the stopped Demo database. Preserve
  the original103-fill backup and a separate stopped188-fill backup.
- [x] Disconnect the perpetual slot through its audited cleanup: cancel managed
  orders, close managed hedge legs, verify fills/fees/flat exposure, then remove
  only that saved bundle. Reconfigure the same account; remain stopped.
- [x] Build a public-config-only Demo overlay for the pinned runtime. Keep
  allocation100 USDT/market, drawdown100/controller and200/global, all exposure
  caps, post-only, maker-fee floor, freshness and ownership controls. For this
  temporary test, use a declared spot inventory target near observed cash weight
  and an explicit interpolation band; this is not wallet rebalancing or a
  production recommendation. Record exact settings and a rollback artifact.
- [x] Deploy the overlay to the same single Cloud Run service, verify metadata,
  credentials, readiness and catalogue hash, then issue a separate audited Start.
- [x] Observe a natural full perpetual entry fill. Match trade ID, base quantity,
  price and original fees to REST. Prove POSITION_HOLD handoff, preserved exposure,
  no duplicate application, safe release of that level and a fresh account check
  before a replacement. On revision68 (`71210c8d…`, same reviewed controller as
  the regular candidate), one BUY and one SELL witness verify four actual filled
  post-only orders, exact original fees and fresh matching account observations.
  Replacement occurs before ordinary60-second refresh. Close timestamps have
  whole-second precision and are not an exact round-trip latency measurement.
- [x] Measure30 minutes of both-sided quoting. Report eligible denominator,
  acknowledged BUY/SELL numerator, explicit policy/readiness exclusions and
  unknown observations separately. Zero eligible samples is unqualified. Missing
  quotes during ordinary replacement still count against the99% eligible target;
  stale data never proves working orders. Retain20s/60s absence diagnostics.
  Preceding revision71 completes1,788 samples over1,799.83 sampled seconds with
  one physical owner, maximum sample gap1.75s and no HALT. Conservative v2 target
  passes: spot1,733/1,745=99.3123% (9 gaps and3 unknown failures), perpetual
  764/767=99.6089% (3 gaps). Eligible fractions of all samples are97.43% and42.90%;
  guards exclude43/1,021 samples respectively. No unknown becomes a success.
  The raw collector's earlier zero-unknown prototype flag stays false; the
  separately frozen v2 scorer applies the declared99% failure-inclusive target.
  Four real filled post-only perpetual orders prove one BUY and one SELL handoff,
  before-refresh replacement, exact original fees and fresh account observations.
  Stopped financial readback matches301 actual fills and zero pending orders.
- [x] Diagnose batch-refresh continuity gap on revision67; source Red7/control1,
  native lifecycle Green106, then a separate invalid-peer Red. Implement opt-in
  bounded rolling refresh without delaying safety withdrawal or trusting a
  pending/invalid peer. Final regressions pass above; targeted packaged rolling
  refresh has118 independent venue samples, no missing sides and32 accepted
  post-only orders, plus stale withdrawal/recovery and Stop. The latest full gate is qualified separately above.
- [x] Run all30 packaged E2E groups, a distinct-image upgrade/rollback scenario,
  unchanged warmup/mixed/burst/60-minute soak/retention budgets, six fault cases
  (five baseline plus volatility defense), and financial
  reconciliation on the pinned candidate. Simulated load fills remain separate
  from actual Demo fills. Preserve failures and rerun only affected gates after
  fixes; a changed runtime requires fresh artifact qualification.
- [x] Restore the regular Demo inventory/spread catalogue with its explicit
  five-second rolling-refresh limit after temporary qualification, verify
  restart/reconciliation and leave the approved Demo trading state explicit.
- [x] Publish a compact acceptance matrix with artifact IDs, results, failures,
  residual limitations and the actual audit principal; close only proven boxes.

The earlier real Google credential/recovery lifecycle is already qualified.
This technical qualification can use the enabled bootstrap administrator and
must retain that actual principal in audit, rather than claim another Google
session. No Google token or exchange secret belongs in reports or command output.

## Runtime incident during confirmation

The revision68 observation is interrupted around minute25 by the operator's CI
rollout to revision69; it is not a30-minute pass. After deployments are paused,
revision70 starts a new observation. Around21:11 UTC /04:11 local, PostgreSQL
response deadlines are exceeded and the engine exits after losing ownership.
Its idle upstream DB session temporarily retains the advisory lock, so replacement
waits and API observations become unknown. Stop is accepted and later acknowledged
at34/34; unresolved effects retain HALT. The specific infrastructure trigger for
the original I/O delays is not established by the orphan-session mechanism test.

- [x] Reproduce retained upstream ownership in an isolated PostgreSQL14 proxy
  test: both directions are blackholed, client discards/exits, upstream stays open.
  Red retains the lock beyond35 seconds. Add a30-second server idle-session budget;
  Green permits takeover before proxy teardown while preserving HALT. Independent
  control proves a healthy owner remains alive and an expired idle reader reconnects.
  Related Green8, liveness Green2 and broader connection-stage PG604 pass.
- [x] Restore stopped103/188/233-fill backups into disposable PostgreSQL16 and
  locate the second defect: both flat hedge summaries become BUY, causing SHORT
  accounting to replace the LONG checkpoint. Native Red1/control4 and database
  Red1 reproduce it. Preserve the original perpetual leg only when flat; native
  Green47 and checkpoint/cleanup PG16 pass, including store/reload/store.
- [x] Support provable spot base-asset fees in recovery at the recorded fill price.
  Red2/controls20, related Green59. Original currency, exact recorded quote fee,
  gross quantity and unsupported third-asset/perpetual fee checks remain enforced.
- [x] Contain remaining venue orders under delegated Demo incident authority:
  Stop/HALT, service-level manual scaling0, backup, account/order identity readback,
  native exclusive owner and cancel-only REST authorization. Five orders have
  independent terminal readback, zero pending orders and per-order maintenance
  audits. No order placement or HALT reset is part of this containment.
- [x] Complete independently verified incident ledger recovery and a disposable
  restore rehearsal before any financial maintenance. Preserve every existing
  order/fill, credentials, risk policy/peaks/latches and the original failed windows.
  Snapshot258 fills all match REST; three later venue fills and eight terminal
  order projections require reconciliation. Native spot completion-order accounting
  matches its retained checkpoint exactly; chronological fill replay alone cannot
  prove that legacy cost basis, so the ordinary Recovery API correctly stays blocked.
- [x] Finish fresh qualification on `f53885fda` / immutable `4198f2fa…` and the
  separate complete revision73 observation. All30 E2E use preceding `8d4a4ffc…`
  for technical upgrade/rollback, not an approved production rollback. Volatility
  defense, security and operator load remain enabled without larger budgets.
  Prior failed/partial3f and69fa image scopes remain failures/partial results.

INFO collection could not be enabled: the current GCP identity lacks
`logging.sinks.update`. The existing project filter was left unchanged; available
WARNING/ERROR, API observations, DB and REST evidence are retained.

Revision71 cold start, real perpetual Disconnect/flat verification/same-account
Setv6 and a no-fill Start/Stop pass. The first new-image observation is interrupted
at266 seconds by the laptop proxy's five-second connection timeout; its log records
a Cloud SQL handshake EOF. Cloud owner remains available, no HALT is recorded,
and audited Stop reaches40/40. Preserve this failed observation. Independent
readback matches276 actual fills and zero pending orders. A read-only observer
transport contract fails5 connections/5 reads, then passes1 connection/5 reads
after replacing NullPool with a one-connection, pre-ping pool. Five-second connect
and statement deadlines remain unchanged; no runtime image/guard change. A new
complete window uses separately frozen observer hashes and counts unknowns as
failures; do not splice the interrupted samples into it.

### Reviewed incident maintenance scope

The operator explicitly delegated full Demo/release closeout and subsequent fault
investigation/TDD fixes. The following technical review and execution use that
delegation, not a new Google login or a separately claimed human approval.
This scope excludes live accounts and changes to shared Cloud SQL resources.

- Require service manual scaling0, stopped/acknowledged34/34, technical HALT,
  no active credential/recovery operation, zero pending venue orders and the
  exclusive database advisory lock. Abort on state, table or exchange drift.
- Retain the post-cancel backup SHA256
  `134d113043279446905190b0687cf1a9fa456f420d3559ec05253c29088aebc6`.
  Restore it into disposable PostgreSQL16 before applying the same transaction.
- Append exactly3 already-executed REST-proven fills and8 terminal status records;
  update only those8 order projections. Preserve all258 original fills, all
  existing order/status history and1325 order identities/terms.
- Repair the three existing position rows. Reconstruct hedge legs from the
  verified233-fill stopped checkpoint plus each later fill exactly once. Preserve
  the native spot cost basis verified from the103-fill checkpoint and68 archived
  completion-order witnesses, then add its one missing SELL fill. Resulting
  hedge quantities LONG0.0021/SHORT0.0015 and fee-deducted BTC wallet cash match
  independent REST; spot net is SELL0.03470199, not a leveraged spot short.
- Remove only technical `halt`/`reason` and set `runtime_active=false` after
  quiescence/reconciliation. Preserve all risk caps, drawdown peaks/latches,
  outflow policy/checkpoints, bindings, encrypted keys, funding and control version.
  Record a maintenance audit with the exact plan SHA and before/after evidence.
  Unrealized PnL uses the declared average-entry reconstruction mark; it is not
  a live market valuation or a promised profit.
- The restore rehearsal passes the full transaction and native position check;
  unexpected position drift rolls back every write, risk HALTs remain blocked,
  and a second application is rejected. Private exact plan SHA256 is
  `b228637a4141704688cfb28b82fa515925f3465a4ce81a1fc7c7d532426664b8`.
  Applied after fresh identical proof at22:15 UTC, maintenance audit107. Independent
  readback verifies261 fills (145 spot/116 perpetual), exact original fees, unique
  identities, zero venue pending orders, hedge quantities and BTC cash0.964246143944.
  The ordinary recovery estimator still conservatively reports
  `position_baseline_unproven` for the historical spot completion-order cost basis;
  this exact repair preserves the independently proved native basis instead.

Resource evidence around the incident has Cloud Run CPU minute means at most
6.56% and memory46.28% of1GiB; these do not establish sub-minute peaks. Cloud SQL
read operations rise from50 to30,201 and63,545 per-minute points around the event.
The recorded backup operation ended about two hours earlier. This is correlation,
not proof of a unique query, network or storage cause; no shared-instance tuning
or deadline relaxation is justified by it alone.

### Fresh-close REST visibility follow-up

After the accepted revision71 window, audited Disconnect reaches
`action_required/cleanup_failed` at closing_positions because an immediate order
read returns explicit OKX51603. Later independent REST proves both actual close
orders filled, positions are flat and their completed records exist. Retry of
the same root operation finishes verification/removal; same-account Setv7 leaves
the bot stopped44/44, both keys Connected and no HALT. Independent readback
matches310 actual fills, exact original fees, zero pending orders/hedge exposure,
and preserved spot cash. The earlier window's 301-fill readback remains separate.

Native Red5/control1 reproduces before/after-ACK visibility and bounded expiry.
A new Linux-image scenario reproduces the same `OKXOrderNotFoundError` and
action-required receipt on immutable `8d4a4ffc…`. The correction waits only for a
fresh close's explicit51603, retaining the same identity inside its original
20-second deadline; it never places again or treats absence/WS completion alone
as REST proof. Other errors retain their original handling. Related Green56
includes fault-fixture controls. The successor passes packaged visibility Green
and all30 E2E, plus two real automatic close/remove/Set cycles without manual
Retry. Latest full-image load/soak/six faults/retention pass above.

The predecessor's all29 E2E and warmup/mixed p99 136.67/212.59ms remain passing
results for that image. Its later full-load attempt is deliberately stopped to
supersede the runtime, not reported as a completed soak or a numeric gate failure.
Keep the original failed real cleanup, same-operation Retry and Linux Red evidence.

### Host sleep during latest-image load

On4198, warmup/mixed/burst pass with p99 260.88/288.88/90.99ms, then the
Mac's closed clamshell enters repeated Maintenance Sleep despite idle assertions.
The soak and local Cloud observer have large wall-clock gaps. Preserve the raw
attempt; it is deliberately interrupted and cannot qualify continuous soak,
faults or retention. A fresh full unchanged load starts at01:27 UTC using a
bounded AC system-sleep assertion, without changing runtime or budgets. The
assertion does not guarantee closed-lid sleep can be prevented; continuity still
must pass. Cloud status after the laptop gap remains revision74/running53,
owner available, no HALT. The old observer timeout is retained. GCP worker
preparation cannot proceed because gcloud requires reauthentication; no new
worker, IAM or shared-database resource change is made.

### Later regular-catalogue restart transition

- [ ] Attribute the01:41 UTC regular revision74 running → stopping → starting →
  guard warmup → running transition. The strict monitor captures desired running
  and running=false, control53/53, unchanged session/revision, available owner,
  no HALT/error; durable event timestamps span about13 seconds. A later read is
  running. No operator command was issued. Preserve the failed monitor sample
  and do not classify this as a harmless probe-only delay.
- [ ] Read matching Cloud logs after gcloud reauthentication, distinguish engine
  watchdog/process/platform restart from a runtime defect, and use TDD before
  any confirmed code correction. The browser connector also cannot initialize.
  A separate5-second read-only incident monitor retains subsequent transitions.

The prior revision73 quote/handoff proof keeps its own complete window. The
regular operating transition remains separate and open; the finite earlier pass
does not prove the restored regular catalogue runs uninterrupted.

Subsequent direct API read identifies new revision `hummingbot-demo-00075-wbf`,
same session/control53, currently running without HALT. This closeout did not
deploy it. Check the external service update/image before attributing the earlier
reinitialization to a runtime defect. Preserve this identity change; new reads
are not evidence that revision74 stayed uninterrupted. Follow-up observer now
records revision changes explicitly instead of discarding them as unknown.

The fresh01:27 UTC full retry completes at02:57 UTC: all four healthy windows,
Random soak3600 and six faults pass;58 continuous soak samples, max gap65.50s.
The earlier clamshell-interrupted attempt remains unqualified. Selective local
cleanup then removes12 unused image identities; in-use/qualified/technical
comparison images and existing container inventory remain.
