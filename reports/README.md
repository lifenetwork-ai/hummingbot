# Reports and verification results

Start here; operational instructions and active plans are in [docs](../docs/README.md).

## Current result

- [Current-image full load and Demo](release/mm-quote-recovery-release.md),
  [load evidence](release/mm-quote-recovery-release-evidence.json), 2026-11-03:
  image660bd38b passes full R1 plus matched shipping R2. Soak p99103ms,
  +14.18MiB retained,4,885 synthetic fills/fees/positions matched and all six
  faults; shipping mixed p9964ms. First burst failure remains retained;
  recovered probes R1 25engine/3API and R2 oneengine remain explicit.

- [Current-image connected Demo](demo/okx-demo-quote-recovery.md),
  [natural-fill evidence](demo/okx-demo-quote-recovery-natural-fill-evidence.json):
  nine fixed cases,600s Random rollover and active spot/both restart pass.
  The first phase has407 canceled orders/zero new fills; a later bounded preset
  verifies five natural maker fills (one spot/four perpetual), exact fees,
  filled restart and11 unique retained rows. Current catalogue200/400 restored;
  canonical service running with trading stopped, no pending orders/HALT.

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
