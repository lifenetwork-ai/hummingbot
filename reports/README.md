# Reports and verification results

Start here; operational instructions and active plans are in [docs](../docs/README.md).

## Current result

- [Latest packaged release and stopped Demo upgrade](release/mm-submission-log-release.md):
  `a190b024` passes packaged logging smoke, three targeted E2E groups, full R1
  (including60-minute soak and six faults) and matched R2. Soak p99 is643ms,
  zero unexpected API errors, retention grows14.39MiB;4,885 fills/positions match.
  Demo is upgraded while stopped with unchanged history/config. Earlier burst
  cause remains open; no new active Demo session or natural fill is claimed.

- [Latest active-session Demo verification](demo/okx-demo-latent-fixes.md): `3f5cbd0f` passes nine
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
