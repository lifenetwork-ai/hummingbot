# Reports and verification results

Start here; operational instructions and active plans are in [docs](../docs/README.md).

## Current result

- **Partial-fill quote-size fix and candidate hour verified:** source `905bff7e3`, candidate354fec1e.
  Two Red BUY/SELL cases;528 native plus two subtests (38 new cases), LIFE lifecycle
  and volatility-defense E2E pass. Quote validity reuses acknowledged in-flight
  remainder; full allocation/exposure reservation and guards are unchanged.
  Matched-settings600s diagnostic passes13,200 HTTP200/no errors or stale and600
  exact synthetic fills. Observed spread-invalid executor references120→35,
  successful submit/cancel calls219→141 each; spot two-sided samples494/597→574/597.
  Controller jitter/probe and quote gaps remain; timing maxima do not all improve.
  Separate unchanged candidate3600s Random hour passes79,200 HTTP200/no unexpected
  HTTP errors or stale, p99 126.88/76.41ms and3,594 exact synthetic fills/fees/positions.
  Retained growth27.59MiB passes64MiB; no unexpected failure/restart. Four measured
  probes recover; two ERROR records refer to one locally blocked SELL absent
  from the simulator. Fine180s trace passes without reproducing the old spike.
  Actual Demo stays stopped on086454de with23 fills/four hashes intact. Full
  R1/R2, canonical rebuild and active candidate-Demo gates are not inherited.
  [Report](performance/mm-partial-quote-size.md), [evidence](performance/mm-partial-quote-size-evidence.json).

  [Final hour/trace](release/mm-partial-quote-final-soak.md), [evidence](release/mm-partial-quote-final-soak-evidence.json).

- **Current-image MM/probe diagnostic:** unchanged `086454de`, bounded600s
  fixed follow_market/both with existing activity/incident/venue instruments;
  13,200 HTTP 200/no errors or stale, p99 347.99/118.91ms and 600 exact synthetic
  fills. One measured probe318.874ms recovers with tick/controller start gaps
  at most1,050.67/1,233.10ms in request-through-recovery context; all five venue
  samples have12 live orders and both markets two-sided. No unexpected restart.
  Whole-window controller568.53ms outlier and recurring spot SELL invalidation
  remain leads; jointly two-sided point coverage is490/597, not continuous uptime.
  No justified runtime/health/guard change; stopped Demo/23 fills/four hashes
  preserved. [Report](investigations/mm-current-probe-correlation.md), [evidence](investigations/mm-current-probe-correlation-evidence.json).

- **Offline preparation standalone hour passes:** unchanged `086454de`,
  3,600s Random soak, 79,200 HTTP 200/no unexpected errors or stale, p99
  260.39/77.27ms and 3,595 exact synthetic fills/fees/positions. All three profiles,
  58 continuous samples/max gap 65.50s and +28.15MiB retention pass unchanged
  budgets. No publication failure or unexpected child restart, including setup.
  Eight slow engine-probe episodes recover; all have local fill-processing and
  independent simulator-fill witnesses, seven also have new accepted orders.
  Per-tick/continuous two-sided quote coverage is not established. Actual Demo is
  restored stopped with 23 fills and four hashes intact. This is a standalone
  hour, not a new full R1/R2 or exclusive historical-cause closure.
  [Report](investigations/mm-owner-publication-timeout.md#full-hour-confirmation-after-offline-preparation), [evidence](performance/mm-offline-history-soak-evidence.json).

- **Offline history preparation corrected:** commits `7bdb5b6f6` / `2b4011556`
  stop the full test service before bulk seed/CHECKPOINT and require normal
  fenced recovery before load. All six maintained callers share the helper;
  169 related native and seven PG tests pass. Two 8 MiB/s controls each seed
  100,000 orders/fills offline and remain stable 65s after restart. The unchanged
  300s workload passes: 6,600 HTTP 200, zero unexpected errors/stale, p99
  113.94/36.87ms and 300 exact synthetic fills. A 120s intentional report-timeout
  test passes with fresh retry, zero stale, 120 matched synthetic fills and
  unchanged engine PID/owner/session. Two recovered short probe episodes remain
  recorded. Shipping image `086454de` and stopped Demo/23 fills/four ledger hashes
  are unchanged. No new hour or exclusive historical-cause closure is claimed.
  [Report and evidence](investigations/mm-owner-publication-timeout.md).

- **Owner publication WAL mechanism reproduced:** a per-container 8 MiB/s PG
  write limit plus the historical bulk seed reproduces the same SELECT/control
  version 2 and `57014`, with same-PID WALWrite samples and a 2,901.57ms SQL span.
  The 65s no-bulk control has no failure/restart; `wal_init_zero=off` does not remedy
  the imposed fault. Seven additional unchanged-setting prefixes and an
  unthrottled 500,000-row control stay clean. A confirmed trace-overwrite bug is
  fixed with Red/Green and 58 related tests. Shipping engine/image, deadlines,
  stopped Demo/23 fills and ledger hashes are unchanged. Historical exclusive
  attribution remains open; no new release acceptance.
  [Report/evidence](investigations/mm-owner-publication-timeout.md).

- **Admin worker lanes verified on086454de:** native2,494 +11 subtests, PG355,
  Swagger, three E2E groups, four report-fault cases,300s probe and unchanged
  hour pass. Hour p99 246.24/88.55ms,79,200 HTTP200/no stale,3,595 exact synthetic
  fills and25.96MiB retained growth. Stopped Demo/hash/config restoration passes.
  One owner-state timeout/restart occurs before the measured window; its exclusive
  cause remains unproven. No new full R1/R2/production acceptance.
  [Report/evidence](performance/mm-admin-worker-lanes.md).

- **Timeout retry image follow-up:** imagef4d90bc7 matches source d2cdf2a36;
  native2,464 +11 subtests, PG354, three E2E groups and four controlled report
  faults pass. The unchanged hour passes:79,200 HTTP200, no errors/stale,
  primary/auxiliary p99 615.65/565.42ms and3,594 exact synthetic fills. The prior
  short probe still fails p99; its traced follow-up passes823.21/734.17ms and
  confirms worker queueing without proving a unique original trigger. No SQL/schema/
  deadline change or new full R1/R2 acceptance. [Report/evidence](performance/mm-daily-conversion-followup.md).

- [Daily conversion/availability follow-up](performance/mm-daily-conversion-followup.md),
  [evidence](performance/mm-daily-conversion-followup-evidence.json): fee-array
  decoding600→200 for200 zero-conversion fills; four paired SQL workloads return
  exactly equal results. Read-only Demo observation reproduces AVAX freshness
  loss. Native2,442 +11 subtests and isolated PostgreSQL345 pass.
  A terminal-before-ACK fixture follow-up adds65 targeted native checks (eleven
  new) and PG7; all three final-fixture E2E groups pass. Rich600s probe fails
  reporting/latency gates, while controlled wait/CPU report faults and connected
  positive spot/perpetual fills/restart pass. The same-image hour fails only
  zero-degraded reporting (two fee SQL timeouts,28 stale replies);3,594 fills/
  fees/positions and25.69MiB retention growth pass. Restoration is independently
  verified; prior failures and unresolved historical causes remain explicit.

- [Controller/persistence image verification](release/mm-controller-persistence-release.md),
  [evidence](release/mm-controller-persistence-release-evidence.json): Demo
  matrix9/9 and three perpetual natural fills checked; no spot fill. The standalone
  60-minute soak fails the zero-degraded-reporting gate after one 2s PostgreSQL
  timeout causes13 stale responses. 3,594 synthetic fills/fees/positions match;
  memory growth28.23 MiB. Probe/guard availability limits remain documented.

- [Controller/persistence optimization](performance/mm-controller-persistence-optimization.md),
  [evidence](performance/mm-controller-persistence-evidence.json): synchronous
  exposure reads3→1; scoped snapshot filter queries2→1, 7.1% fewer cohort SQL
  statements with identical snapshot output. Raw negative spot observations can
  no longer be masked by BUY reservations. Wall-time samples are mixed; source
  verification and image/load qualification are kept separate in the report.

- [Source quote-wait diagnostics](investigations/mm-quote-wait-diagnostics.md),
  [evidence](investigations/mm-quote-wait-diagnostics-evidence.json): per-side
  wait evidence, sampled age and English guidance; native2,429 +11 subtests,
  focused44, relevant PG19 and Swagger pass. Prior accepted image is unchanged;
  new runtime fields require rebuilding, with no new image/load/Demo claim.

- [Guard/recovery coverage](investigations/mm-guard-recovery-coverage.md),
  [evidence](investigations/mm-guard-recovery-coverage-evidence.json):36 added
  native/PG cases, related native541 +2 subtests, full PG329 and focused PG10 pass
  under their stated collection scopes. No production logic change or new
  image/load/Demo claim.

- [Final-image probe/MM correlation](investigations/mm-checkpoint-probe-correlation.md),
  [evidence](investigations/mm-checkpoint-probe-correlation-evidence.json): 600s
  diagnostic on `51d4d879`, API p99 71.41ms and 600 matched synthetic fills.
  Both probe episodes retain tick/controller/order progress; no new runtime
  defect or threshold change. Historical MM timing remains unavailable.

- [Final checkpoint image release](release/mm-checkpoint-final-release.md),
  [evidence](release/mm-checkpoint-final-release-evidence.json): current `51d4d879`
  passes its own full R1/R2, with soak p99 235 ms, +14.14 MiB retained memory,
  4,885 matched synthetic fills and six fault groups. Later read-only REST
  matches all seven natural fill IDs, quantities, prices and original fees.
  Residual probes remain R1 20 engine/one API, R2 15 engine, all recovered.
  Local service is restored idle with no HALT and unchanged ledger hashes.

- [Shutdown checkpoint fix and reviewed Demo recovery](demo/okx-checkpoint-recovery.md),
  [evidence](demo/okx-checkpoint-recovery-evidence.json): source `06ab51b17`,
  native2,371 +11 subtests, PG321, Swagger and three packaged E2E groups pass.
  Current local image `51d4d879` passes stopped upgrade/restart with unchanged
  ledger hashes, no HALT and trading stopped. Prior `7dca3ce9` passes nine active
  Demo cases/Random/restart. Seven natural fills match independent REST order
  aggregates/account state; subsequent per-trade REST proof is complete above.

- [Expiry-fix offline load](release/mm-okx-expiry-load.md) and
  [evidence](release/mm-okx-expiry-load-evidence.json): image `7dca3ce9`
  passed the full local R1, isolated repeat warmup/mixed and clean R2. The
  original R1 warmup overlapped a short clone dry run; see the report for scope,
  recovered probes and the separately repeated windows.
- [Demo expiry incident](demo/okx-expired-submission-investigation.md) and
  [review proposal](demo/okx-expiry-incident-recovery-proposal.md): the prior
  image failed at Random rollover. Exact intent repair and separate checkpoint
  reset were subsequently approved and applied; current recovery status is above.

- [Checkpoint and background-poll source optimization](performance/mm-checkpoint-poll-optimization.md),
  [SQL evidence](performance/mm-checkpoint-poll-evidence.json), 2026-11-03:
  atomic position batches, no idle reconciliation control-row lock, funding
  discovery with existing-index seeks. Source and later image/load results are
  detailed in the linked reports above; prior660 measurements remain scoped to
  the preceding image.

- [Earlier image full load and Demo](release/mm-quote-recovery-release.md),
  [load evidence](release/mm-quote-recovery-release-evidence.json), 2026-11-03:
  image660bd38b passes full R1 plus matched shipping R2. Soak p99103ms,
  +14.18MiB retained,4,885 synthetic fills/fees/positions matched and all six
  faults; shipping mixed p9964ms. First burst failure remains retained;
  recovered probes R1 25engine/3API and R2 oneengine remain explicit.

- [Earlier connected Demo](demo/okx-demo-quote-recovery.md),
  [natural-fill evidence](demo/okx-demo-quote-recovery-natural-fill-evidence.json):
  nine fixed cases,600s Random rollover and active spot/both restart pass.
  The first phase has407 canceled orders/zero new fills; a later bounded preset
  verifies five natural maker fills (one spot/four perpetual), exact fees,
  filled restart and11 unique retained rows. Current catalogue200/400 restored;
  at the time. Current Demo state is described in the recovery report above.

- [Quote replacement after terminal confirmation](investigations/mm-quote-recovery.md),
  [evidence](investigations/mm-quote-recovery-evidence.json), 2026-11-02:
  fixed five-second executor idle wait removed after all tracked orders complete.
  Native2,356 plus11 subtests, PG296, Swagger and three packaged E2E groups pass.
  Matched180s diagnostics: median post-terminal wait4,993→99ms, longest empty-point
  span6.023→2.006s;180 synthetic fills reconcile per run. Guard gaps and quote SLO
  remain explicit; this checkpoint precedes the subsequent full/load/Demo scope above.

- [MM execution and quote observations](investigations/mm-trading-continuity.md),
  [evidence](investigations/mm-trading-continuity-evidence.json), 2026-11-02:
  opt-in bounded diagnostics;946 CLI tests and180s offline probe pass. A separate
  confirmed2.272s engine pause is detected by tick/controller gaps and independent
  live-quote samples. Its empty-sample follow-up is investigated above; production
  quote-coverage policy remains open. Rebuilt diagnostic `4ef2c50a` has
  matching reviewed runtime/config source, not inherited full-release/Demo acceptance.

- [Runtime logic final-image load rerun](release/mm-runtime-logic-release.md),
  [evidence](release/mm-runtime-logic-release-evidence.json), 2026-11-02:
  unchanged full R1 and matched shipping R2 pass on `95cde31c`. Soak p99 465ms,
  zero API errors, +12.69MiB retained, 4,885 matched fills/positions and all six
  faults; R2 mixed p99 65ms. Recovered probes remain R1 38 engine/two API and
  R2 two engine; no unique cause or new connected Demo/deployment claim.

- [Runtime logic audit](investigations/mm-runtime-logic-audit.md),
  [evidence](investigations/mm-runtime-logic-audit-evidence.json), 2026-11-02:
  fixes durable HALT leaving active core/resting orders and equivalent Decimal
  fee text rejecting duplicate fills. Native 2,310 plus 11 subtests, PG 296,
  Swagger, two targeted E2E groups and packaged external HALT/restart pass.
  Those targeted checks preceded the separate full load rerun above; neither
  task includes new connected Demo acceptance.

- [Further residual-probe investigation](investigations/mm-engine-residual-probes.md),
  [evidence](investigations/mm-engine-residual-probes-evidence.json), 2026-11-02:
  redundant observer owner read removed, real PG/HTTP Red/Green and broad source
  checks pass. Candidate `18422eca` passes MM lifecycle and a 300s ordinary load
  (p99 61ms, 300 matched fills/positions, zero engine/one recovered API probe).
  No executor cache introduced; no new full release/hour/Demo acceptance.

- [Engine status-publication follow-up](investigations/mm-engine-status-publication.md),
  2026-11-02: a real PostgreSQL/HTTP Red/Green proves one avoidable on-loop wait;
  source, rebuilt image smoke and three E2E groups pass. Same-base 600s controls
  both pass, but recovered probes remain 2 vs 2. Full unchanged R1 and matched
  shipping R2 independently pass on `e2d393b1`: soak p99 113ms, +13.18MiB retained,
  4,886 matched fills/positions and six faults; shipping mixed p99 72ms. Residual
  probes are 15/6 with no restart; unique cause stays open. No new Demo/deploy.

- [Controlled engine-probe follow-up](investigations/mm-engine-probe-controlled.md),
  [evidence](investigations/mm-engine-probe-controlled-evidence.json), 2026-11-02:
  all54 historical recovered episodes reviewed. Three matched600s controls pass
  with1/1/4 recovered probes (ordinary/scalar/GC+incident), p99 247/360/383ms,
  zero errors and exact fill/position checks. No unique cause or new shipping
  defect is established. Current-image Demo REST/WS and read-only recovery-copy
  checks pass; original volume/config unchanged, no service/trading start.

- [Reporting/startup optimization and final image gates](performance/mm-reporting-startup-optimization.md),
  [evidence](performance/mm-reporting-startup-evidence.json), 2026-11-02:
  image `39198499`, native2,265 plus11 subtests/PG282/Swagger and three E2E
  groups pass, followed by full R1 and matched shipping R2. Soak p99 is553ms,
  zero API errors, retention +14.03MiB and4,886 reconciled fills/positions;
  shipping mixed p99 is81ms. Instrumented R1 has54 recovered probe episodes,
  shipping R2 has0; no unique cause is claimed. No new Demo/cloud deployment.

- [Probe diagnostics follow-up](investigations/mm-engine-probe-latency.md), 2026-11-02:
  61 native tests, clean-image smoke and300s diagnostic pass (p99 158ms, zero
  errors,300 matching fills). Native hot-leaf trace overhead is556→87ms per50k
  calls; no trading-logic change. One short probe timeout recovers; cause remains
  open. Docker cleanup removed the previous Demo container/image; its persistent
  DB volume remains. The new diagnostic image is not promoted to Demo/release.

- [Prior packaged release and stopped Demo upgrade](release/mm-submission-log-release.md):
  `a190b024` passes packaged logging smoke, three targeted E2E groups, full R1
  (including60-minute soak and six faults) and matched R2. Soak p99 is643ms,
  zero unexpected API errors, retention grows14.39MiB;4,885 fills/positions match.
  Demo is upgraded while stopped with unchanged history/config. Earlier burst
  cause remains open; no new active Demo session or natural fill is claimed.

- [Prior active-session Demo verification](demo/okx-demo-latent-fixes.md): `3f5cbd0f` passes nine
  fixed cases, Random rollover and spot/both active restart. 381 post-only orders
  independently confirmed canceled; balances/positions/history match. No new fill,
  so earlier positive-fill evidence is not relabelled. Service is trading-stopped.

- [Prior latent-fix image load rerun](release/mm-release-closeout.md#2026-11-01-latent-fix-candidate-rerun):
  `3f5cbd0f` latest full R1 and matched shipping R2 pass. First burst failure
  remains unexplained; Random long-soak coverage is two profiles only.
  Two retired bot images removed; Demo/container images and volumes preserved.

- [Latent-bug fixes](implementation/life-mm-logic-review.md#2026-11-01-latent-bug-audit-and-fixes):
  B1–B4 fixed/committed; native2,208/PG279, Swagger and3 targeted image groups pass
  on `3f5cbd0f`. Original source-fix scope retained; long-load follow-up is recorded above.
- [Prior accepted release](release/mm-release-closeout.md): image `5973410d`, full R1
  and matched shipping R2 pass. Earlier failures remain recorded.
- [Historical-cause review](investigations/mm-historical-cause-review.md): latest
  diagnostic fix and explicit H1/H2/H3 attribution limits; accepted R1/R2 unchanged.
- [Implementation progress](implementation/LIFE_OKX_PROGRESS.md): chronological
  delivery journal, with a current summary at the top.
- [Ordered flow follow-up](performance/life-mm-runtime-optimization.md#2026-11-01-ordered-implementation):
  all three changes complete; native2178/PG268 and three image scenarios pass.
  New candidate `4971ea59` has targeted verification, not a new hour-soak acceptance.

## Browse by topic

| Folder | Contents |
| --- | --- |
| [release](release/README.md) | Current closeout, supervision acceptance and earlier image-specific release attempts |
| [investigations](investigations/README.md) | Latency, probe failures, reporting SQL, incident capture and test-instrumentation fixes |
| [demo](demo/README.md) | OKX Demo preflight, sessions, natural fills, restart and funding evidence |
| [implementation](implementation/README.md) | Feature delivery, audits, API/UX review and progress journal |
| [platform](platform/README.md) | PostgreSQL, Docker packaging, logging and Cloud Run readiness |
| [performance](performance/README.md) | Load/soak, memory, optimization and synthetic economic stress |

Keep each report and its portable evidence together. JSON evidence is preserved
byte-for-byte; paths and commands recorded inside it describe the original run
layout. To locate a moved artifact, use its unchanged filename in the topic index.
A historical pass does not certify a newer image, and old pending checkpoints
do not override the current closeout. Cloud and FE work retain their own scope.

Raw logs, ledgers and dumps stay under ignored `build/`; credentials and private
account configuration stay outside the checkout. Future reports belong in the
appropriate topic folder, with a link in that folder's index. Keep this main
index focused on current results and topic navigation.
