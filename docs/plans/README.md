# Plans and remaining verification

Updated 2026-11-09 after new-image load verification. Start with the [current image result](../../reports/release/mm-runtime-followup-load.md)
and [operating guides](../README.md). Detailed completed TDD plans remain in
[Git history](../../reports/README.md#historical-records). Historical unchecked
boxes retain their original image/date scope; removing files does not close
unresolved work. Current outstanding items are listed below.

## Current state

- [x] New source hot-path allocation phase: bounded book payload collection,
  disabled-debug formatting and discarded performance defaults; native 972 pass
  + 9 subtests. [Measured scope](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md).
- [x] Quote/anchor source phase: remove superseded spread calculations, reuse
  current bootstrap allocations and avoid whole-book/DataFrame anchor fallback.
  Native **1,010 pass + 9 subtests**; exact Decimal and failure behavior covered.
- [x] Package the new source as image `96368970…`, build `8544488ef`, and verify
  1,225 packaged hashes plus four image-level behavior/counter contracts.
- [ ] Close the new image's full-performance gate: burst main/auxiliary p99
  **1,084.01 / 1,151.07 ms** fail the original 1,000 ms budgets. Warmup/mixed/
  soak3600 and all five faults pass; 4,884 simulated fills reconcile, retained
  growth 26.09 MiB/64 MiB. Keep original thresholds and this failed attempt.
- [x] Fix the reproduced older seed-E2E fixture omission with Red/Green;
  live fails/demo passes before, 14 related tests pass after `556d30bb7`.
  No engine/controller/recorder or runtime credential-gate change.
- [x] Execute twenty-seven unique available E2E groups across attempts:
  corrected `all` fails inventory shutdown after fourteen passes; thirteen
  remaining/targeted groups pass. The failed `all` remains failed.
- [x] TDD-confirm/fix signal/Event reentrant shutdown lock (`1916e048e`), Red1 /
  related Green75. The preceding nonzero exit remains unproven attribution.
- [ ] Build and qualify a new image for that signal fix; `96368970…` predates it.
- [ ] Run upgrade/rollback E2E if full upgrade qualification is required; no
  compatible previous bot image exists in the current Docker daemon.
- [x] Preserve previous accepted image `a6fe88c0…` and its complete quantile-fixture
  gate as historical evidence in Git `8544488ef` at current release paths. Its
  mixed/burst/soak p99 was 366.46/168.04/287.63 ms, three recovered probes.

- [x] Quantile-export work: 80 targeted passes before load, 81 after one added
  long-ring integer case. Paired component CPU decreases on full-size rings;
  small-ring cost/outliers are retained. No production/image or threshold change.
- [x] Preserve failed repeat in Git `41d290393`: mixed main/auxiliary p99
  1,315.91/1,169.10 ms, seven engine probes across warmup/mixed; later phases
  unrun. The new complete gate does not retroactively pass that attempt.
- [x] PostgreSQL/Cloud Run source port, three MM scenarios, fixed/Random sessions,
  spot/perpetual/both, operator APIs and risk controls are implemented.
- [x] Reporting-copy source: native 2,788 + 11 subtests / related PG 36 pass,
  eight new cases. Strict JSON detachment remains; paired component CPU improves
  with mixed allocation results.
- [x] Fresh write-fence projection source: native 2,780 + 11 / full PG 556 pass,
  nine new cases. Fresh authority remains; unused proof JSON is omitted.
- [x] Previous image `a6fe88c0…` packages both changes, including earlier ownership-read
  and recorder optimizations. All 1,224 packaged hashes match through measurement.
- [x] Earlier fixture: unchanged baseline, 60-minute Random soak and five faults; four targeted
  E2E groups, enhancement90 and a separate activity300 diagnostic pass first attempts.
- [x] Earlier fixture: soak p99 406.14 ms, zero unexpected errors, 24.26 MiB retained growth;
  4,885 exact simulated fills/fees/positions. Eleven soak engine probes and three
  mixed engine probes recover, without healthy child replacement. Burst p99 and
  measured probe count regress versus rollback `e00cef38…`; no uniform speedup.
- [x] Activity diagnostic observes completed cycles and two-sided venue point
  samples during its two probes. Refresh observations/coverage limits remain;
  this does not prove continuous quotes or instrument the baseline retrospectively.
- [x] Historical cleanup preserved the then-existing candidate/rollback and
  container/volume identities. Those resources are absent from the current daemon;
  this run preserves its unrelated images/volume and performs no image pruning.

## Remaining external acceptance

- [ ] Real Google sign-in/certificate discovery and real GCP KMS/IAM with synthetic
  credentials, when project/client resources are available. Offline Google/local
  KEK checks do not close this. [Credential plan](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
- [ ] Reviewed existing-Demo managed-credential cutover, if that migration is chosen.
  It has not been performed by the new local gates. [Demo status](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md).

## Policy and evidence still unresolved

- [ ] Exclusive attribution of the historical probe-frequency/p99 regression
  remains unproven. The failed 2.6-second minute, prior 6→14 probe increase and
  bounded captures retain their original scope in Git `41d290393`. The prior
  accepted gate has mixed 1 / soak 2 engine probes. The new candidate has twelve
  healthy recovered transitions (ten engine/two API), and burst p99 fails. Power
  changes from battery to AC during the new soak. Lower component CPU/GIL exposure is measured, but it does not establish
  one historical cause or a uniform whole-system gain.
  [Current evidence and limits](../../reports/release/mm-runtime-followup-load.md#recovered-probes-and-comparison).
  The separate activity300 point samples and ABBA120 audit retain their own
  scopes; they do not instrument the latest baseline retrospectively.
  [Code audit](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md#combined-image-regression-audit--2026-11-07).
- [ ] Agree a production quote-coverage SLO separating normal replacement,
  guard restrictions and listing bootstrap. HTTP200/PID stability and point
  samples do not establish continuous two-sided quotes. The [review criteria](../fe-integration.md#reviewing-quote-availability)
  distinguish replacement progress, guards and unknown/stale evidence without
  silently setting numeric targets. The 120-second warning is not that SLO.
  [Original item](../../reports/README.md#historical-records) (Git: `docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md`).
- [ ] Exclusive attribution of the older p99/owner-publication trigger still lacks
  contemporaneous host/storage/wait proof. Controlled WAL/loop evidence establishes
  mechanisms, not that unique historical cause. [Original investigation](../../reports/README.md#historical-records)
  (Git: `docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md`).
- [ ] The historical OKX expiry-response shape still lacks the isolated witness
  requested by that plan; simulated parser/transport cases are separate.
  [Original item](../../reports/README.md#historical-records) (Git: `docs/plans/OKX_EXPIRED_SUBMISSION_TDD_PLAN.md`).

The prior controller-image natural-spot/clean-soak/guard-wait boxes remain in the
[original image-scoped Demo plan](../../reports/README.md#historical-records)
(Git: `docs/plans/OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md`). A newer offline pass does
not retroactively pass that image or certify a connected Demo run.

The historical rollback record `e00cef38…` retains its original full gate in Git `745a86e3c` at the
current report/evidence paths. The still earlier 9.5-second PID1 shutdown poll
failure and unchanged passing retry remain in Git `ff6b85ded`; the unique cause
is unproven. Earlier targeted restart E2E retains its original pass. Capture Docker/PID
exit-state timing if that failure recurs. Source failures, rejected drafts and
component measurements remain in the [source report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md).

## Retained plans

- [PostgreSQL / Cloud Run](POSTGRES_CLOUD_RUN_PORT_PLAN.md)
- [Admin identity and encrypted credentials](ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md)
- [Actual OKX Demo versus offline image acceptance](OKX_DEMO_CURRENT_IMAGE_ACCEPTANCE.md)
- [Runtime remediation checklist](MM_RUNTIME_FOLLOWUP_REMEDIATION_TDD_PLAN.md)

Production deployment and FE browser acceptance are separate from these local
checks. Their procedures are in [Cloud Run operations](../cloudrun.md) and
[FE integration](../fe-integration.md).
