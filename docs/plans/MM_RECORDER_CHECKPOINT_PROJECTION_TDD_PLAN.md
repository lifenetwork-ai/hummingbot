# Recorder checkpoint projection — TDD

Date: 2026-11-07 (Asia/Saigon). Follow the
[current-image code audit](../../reports/investigations/mm-cleanup-state-probe-code-audit.md).
Baseline runtime `2f6cbd257`, accepted image `f74d2a2a…`.

Scope: reduce redundant checkpoint reads when recording a fresh order intent.
Ordinary fill/checkpoint writes retain full reads and unchanged-JSON detection.
Keep connector state on its event loop, transaction/owner/control
fences, durable-before-send, terminal-fill proof and lost-ACK tracking unchanged.
No new worker, cache, schema, dependency or health-threshold change.

- [x] Measure baseline checkpoint SQL/payload/time/allocation with small and
  controlled large saved-state fixtures before changing production code.
- [x] Red: a fresh intent with complete current tracking must not materialize unused
  old JSON. Test acknowledged/current/pending/empty/new-row paths and exact writes.
- [x] Green: defer old JSON only for fresh, locally tracked intent writes;
  retrieve it when missing pending identities need merging. Ordinary checkpoint,
  untracked-intent and full recovery reads stay full.
- [x] Cover lost ACKs, multiple missing identities, config/market scope,
  writes, cold restore, mutation isolation, stale ownership and read/commit failures.
- [x] Compare paired baseline/projected measurements without timing pass thresholds;
  retain query-count/payload tradeoffs and avoid a claim that all probe causes are fixed.
- [ ] Run appropriate recorder/ownership/cleanup/terminal/native and PostgreSQL
  regressions, record evidence, close verified checklist items and commit.

Source phase: four fresh-intent Red cases fail on the original recorder; all22
new scoped cases pass after the narrow correction. Two broader experiments were
rejected: general lazy replacement rewrites unchanged large JSON; a server equality
projection avoids that rewrite but still increases large unchanged-checkpoint cost.
They are not shipping behavior. The final patch uses SQLAlchemy's existing deferred
column loading only for a new tracked intent, with no equality query, hash or cache.
Matched seven-pair measurements retain small/large/fallback tradeoffs; no MM p99
or probe-removal claim. Broad regression and final documentation remain below.

Any shipping runtime change requires separately identified image qualification
before deployment. Previous image/hour acceptance does not automatically qualify
new source. Existing Demo, real exchanges, Google/GCP and production are excluded.
