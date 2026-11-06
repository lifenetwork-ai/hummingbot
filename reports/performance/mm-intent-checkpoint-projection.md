# New-intent checkpoint projection

Date: 2026-11-07 (Asia/Saigon). Source `c9889da28`.
[Plan](../README.md#historical-records) (Git: `docs/plans/MM_RECORDER_CHECKPOINT_PROJECTION_TDD_PLAN.md`),
[portable evidence](mm-intent-checkpoint-projection-evidence.json).

## Accepted change

The recorder loads the previous `market_states.saved_state` JSON before each
checkpoint. A fresh tracked order identity requires a new snapshot, so its old
JSON often goes unused. The patch reuses SQLAlchemy deferred column loading in
this specific new-intent path. It still reads the old JSON when missing pending
identities must be retained. Ordinary checkpoint/fill writes, untracked-intent
fallback and default cold recovery retain full reads.

No worker, cache, schema, setting, health deadline or dependency is added. Connector
state stays on the engine loop; the transaction commits before transport, under
the same owner/control/row fences. This removes redundant work on the intent path
identified in the preceding [probe audit](../README.md#historical-records) (Git: `reports/investigations/mm-cleanup-state-probe-code-audit.md`).
The synchronous fill/ownership path, including the retained 336 ms fill callback,
is not moved off the loop or declared fixed.

## TDD and regression results

Four fresh-intent cases fail on the original source because the SELECT returns
unused tracking JSON. The narrow patch passes all **22 new PostgreSQL cases**:
18 behavioral cases plus four paired measurement cases. They cover first/existing
rows, acknowledged and covered pending identities, missing pending merge/cold
restore, unchanged ordinary checkpoints, absent current tracking, read/commit
rollback, owner loss, config/market scope, changed existing values and duplicate
identity rejection. Existing large-cohort tests retain sibling-intent admission
and one scoped filter per snapshot across repeated writes.

Frozen-source broad verification:

| Check | Result | Log |
| --- | --- | --- |
| Native Cloud Run regression | 2,723 tests + 11 subtests pass | `/tmp/hbot-intent-checkpoint-final-native.log` |
| Full PostgreSQL suite | 518 pass, including the 22 new cases | `/tmp/hbot-intent-checkpoint-final-postgres.log` |
| Separate standalone paired measurement | Four pass; overlaps the measurement scope above | `/tmp/hbot-intent-checkpoint-accepted-paired.log` |
| Formatting, lint and commit hooks | Pass | Source commit `c9889da28` |

No counts add overlapping focused cases into the broad totals. PostgreSQL runs
against disposable local databases; no actual exchange account or existing Demo
ledger is changed.

## Paired measurements and limits

After broad regressions complete, run seven alternating full/projected pairs per
fixture on the committed source. The full-reader counterfactual forces the existing
default reader; recorder/owner/transaction code is otherwise the same. Measurements
time `record_order_intent()` DB work, not a REST request, a full order lifecycle or
MM p99. Timing is evidence, never a test threshold.

| Fixture | Full p50 | Candidate p50 | Old JSON reads | SQL statements |
| --- | ---: | ---: | --- | --- |
| Small new intent | 19.30 ms | 18.43 ms | 1 → 0 | 11 → 11 |
| New intent, approximately 1 MiB old/current snapshot | 52.95 ms | 46.61 ms | 1 → 0 | 11 → 11 |
| Large unchanged ordinary checkpoint | 21.15 ms | 19.91 ms | 1 → 1 | 5 → 5 |
| New intent with missing pending tracking | 55.06 ms | 55.37 ms | 1 → 1 | 11 → 12 |

The large-intent fixture avoids returning/decoding approximately 1 MiB of old JSON;
the new large snapshot still has to be written. Statement count does not decrease.
Missing pending tracking pays one conditional lazy SELECT so recovery proof is
retained. The ordinary-checkpoint row runs the same full-reader code in both modes;
its timing difference is a control observation, not an optimization claim.

All earlier measurements remain in the evidence. The earlier small-intent paired
p50 was **17.70 → 20.51 ms**, whereas the final standalone repeat is **19.30 →
18.43 ms**. Do not claim a stable speedup for small snapshots. Earlier large-intent
p50 was **53.51 → 44.52 ms**. These short local samples are not a universal latency
bound or proof of reduced probe incidence. The 1 MiB payload is synthetic, not an
observed size or exclusive explanation for the six prior probe events. Initial
generic-checkpoint allocation measurements are separately scoped; no new RSS or
retained-memory improvement is claimed.

## Rejected experiments retained

1. **Defer every checkpoint:** an unchanged large snapshot is unnecessarily
   rewritten. Two additional Red cases catch this; paired p50 is 20.41 → 42.53 ms.
2. **Add a PostgreSQL equality projection:** it avoids that rewrite but still
   makes the large unchanged case slower (22.00 → 47.49 ms). This approach is
   removed. The final patch has no server equality, content hash or data cache.
3. An intermediate test expected one lazy read across two checkpoint calls.
   That assumes ORM object reuse across flushes; the correct bound is per call.
   The intermediate 124-pass/one-failure result stays recorded, without adding
   cross-call caching to satisfy the assertion.

Exploratory broad native/PG runs are not final-source acceptance. Source scope
changed while exploring; the PG attempt is deliberately interrupted after 74 passes.
Both are retained separately. Only the frozen-source broad logs in the table above
qualify the accepted patch.

## Release boundary

This is source-level acceptance. Accepted image `f74d2a2a…` is unchanged; it does
not contain this patch. No new container build, E2E, hour soak, real Google/GCP or
Demo cutover is claimed. Build and qualify a separately identified image before
deploying the new source. The earlier six recovered probes remain qualified;
their exclusive causes and continuous quote timing are not closed by these tests.
