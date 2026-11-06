# Runtime follow-up remediation — TDD

Date: 2026-11-07 (Asia/Saigon). Baseline `b9b73366d`, runtime recorder `c9889da28`.
Follow the [review](../../reports/investigations/mm-runtime-followup-code-review.md).
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

- [ ] Run required final native and PostgreSQL regressions on frozen source;
  publish findings, retained failures and source/image acceptance limits; commit.

No real Google/GCP/OKX operation, Demo data repair, production deployment or
inherited new-image/hour acceptance. Rebuild/qualify changed runtime separately
before deployment. Keep health thresholds, durability and DB pool limits unchanged.
