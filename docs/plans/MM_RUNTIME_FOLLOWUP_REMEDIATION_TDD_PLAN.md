# Runtime follow-up remediation — TDD

Date: 2026-11-07 (Asia/Saigon). Baseline `b9b73366d`, runtime recorder `c9889da28`.
Follow the [review](../../reports/README.md#historical-records) (Git: `reports/investigations/mm-runtime-followup-code-review.md`).
Reuse existing service incidents, risk guards, supervisor and native reports.

## R1 — Availability boundaries (F1/F2)

- [x] Red: stopped malformed checkpoints and live risk-status collection/validation
  faults must produce explicit unavailable diagnostics without an observer exception.
- [x] Preserve raw checkpoints; block running intent on invalid durable risk data
  through existing recovery HALT. Never reset guards or invent policy/binding values.
- [x] Preserve cancellation, ownership loss, SQL/recorder/checkpoint failure and
  cleanup/restart fences; do not catch critical failures as optional reporting.
- [x] Green: healthy recovery clears observational incidents; real PostgreSQL
  verifies stopped control availability, retained checkpoint/HALT and blocked sends.
- [x] Record scoped evidence, update checklist and commit this phase.

R1 source checks: initial 12 Red failures, seven additional checkpoint/Stop Red
failures and two corrected operator-view Reds. Thirty native and five PG cases
are added. Related native87 / PG10 pass before final frozen-source broad checks.
Invalid checkpoints retain their raw data; running intent uses existing HALT.
View failures return null with scoped incidents, while critical exceptions and
real cleanup/checkpoint writes still propagate. Operator guidance is English.

## R2 — Steady readiness (F3)

- [x] Red/Green: remove only the duplicate steady no-bootstrap readiness traversal;
  preserve strict final readiness, preparation/anchor writes and bootstrap checks.
- [x] Keep per-order/after-throttle checks fresh; no cross-call cache. Measure
  reference/exposure call counts and verify recovery/failure paths; commit the phase.

## R3 — Remaining report costs (F4/F5/F6)

- [x] Measure actual valuation-ready/report/serialization costs before selecting
  further changes. Retain small/large fixture and freshness/error tradeoffs.
- [x] TDD any justified narrow optimization; keep new/held exposure, finite-value
  rejection, fees, scope filters, strict serialization and mutation isolation.
- [x] Document intentionally deferred changes if measurements do not justify them.

## Verification

- [x] Run required final native and PostgreSQL regressions on frozen source;
  publish findings, retained failures and source/image acceptance limits; commit.

No real Google/GCP/OKX operation, Demo data repair, production deployment or
inherited new-image/hour acceptance. Rebuild/qualify changed runtime separately
before deployment. Keep health thresholds, durability and DB pool limits unchanged.

Frozen source `504343b9b`: native **2,780 + 11 subtests**, full disposable
PostgreSQL **523**, Swagger and formatting/lint pass. New coverage totals 57 native
and 5 PG, included in those totals. Phase commits: availability `45707b06e`, steady
readiness `1f4f19de6`, fresh exposure/held summary reuse `504343b9b`.
See [report](../../reports/implementation/MM_RUNTIME_FOLLOWUP.md) and
[evidence](../../reports/implementation/mm-runtime-followup-evidence.json).
JSON-copy consolidation is deliberately deferred after measurement. No changed
image/E2E/hour gate or unique historical-probe attribution is claimed.


## Image/load follow-up — requested 2026-11-07

The source-only results above retain their original scope. New image verification
is recorded separately; keep the previous gate's workload, budgets and failures.

- [x] Build/pin an amd64 image from the verified source; record packaged source
  hashes, runtime smoke and targeted image E2E evidence.
- [x] Run unchanged full baseline with operations/security and bounded incident
  capture: warmup300, mixed900, burst90, soak3600 and all five fault windows.
- [x] Exercise enabled drawdown/report paths in a separate bounded diagnostic;
  do not use it to replace the unchanged baseline or claim continuous MM activity.
- [x] Publish image-specific results/limitations and close only verified items.
- [x] After tests finish, remove exact unused bot images; retain current candidate,
  accepted rollback, all container-referenced images, other projects and volumes.

Image `bf4e1f77…` passes the new full gate (soak p99 306.91 ms, zero unexpected
healthy errors, 21.89 MiB retained growth, 4,885 exact simulated fills), four targeted
E2E groups, enabled-enhancement90 and all five faults. Three recovered healthy
probes remain qualified. One unused image removed; container/volume inventories
retained. See [image report](../../reports/release/mm-runtime-followup-load.md) and
[evidence](../../reports/release/mm-runtime-followup-load-evidence.json).
