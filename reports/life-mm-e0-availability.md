# E0: PostgreSQL service availability

Date: 2026-10-26 (Asia/Ho_Chi_Minh).
Status: E0 complete and verified locally.
Baseline: `3401c4412`. Scope: E0 of the
[incremental enhancements plan](../docs/plans/LIFE_OKX_MM_ENHANCEMENTS_TDD_PLAN.md).

## What changed

Optional reporting failures no longer escape through the owner loop and terminate
an otherwise healthy service. Reporting snapshots retain their last observation
but explicitly lose freshness on projection failure. Optional engine-detail
projection failures retain only known internal control flags; missing flags remain
unknown, so a report failure cannot manufacture successful command completion.

Daily balance writes use a savepoint within the existing fenced publication
transaction. Malformed optional values roll back their own observation, while
heartbeat and truthful command receipts can commit. Real PostgreSQL invalid-NUMERIC
tests cover this behavior. Connection errors, critical receipt failures and commit
errors still propagate and roll back the required transaction.

The host schedules optional checkpoints once per minute and immediately when the
reporting day, verified account/environment or currencies change. A failed fenced
publication cannot advance that schedule. Invalid samples back off while retaining
their incident; skipped attempts do not falsely report recovery. Live balance
observations and required heartbeat/receipt publication keep their original cadence.

Observational reconciliation claiming now runs as one retained background task,
so its read/claim wait cannot occupy the owner loop. Recoverable failures retain
a visible reason and capped backoff. A dedicated ownership failure still reaches
the host; it is not converted into an optional reporting warning. Existing funding
collection remains read-only and reports its errors without changing trading intent.

LIFE initialization retries only explicitly read-only mode discovery, public
instrument metadata and reference-book acquisition. Each attempt has a 30-second
deadline; backoff starts around one second and is capped at thirty seconds with
jitter, respecting available Retry-After instructions. Stop cancels the same task.
Mode/leverage mutations and uncertain acknowledgements are never replayed by the
read-retry helper. Permanent HTTP rejection is not retried as a temporary failure.

The service supervises retained controller/clock/connector task handles and
controller progress. Required task failures block both intent admission and final
transport authorization, enter durable recovery HALT and preserve positions.
Cancellation remains permitted by the existing ownership rules. A controller's
declared read/backoff wait is not mistaken for a stalled control loop. File-mode
controller restart behavior remains available.

Admin `/status.service` exposes bounded, sanitized incidents, UTC observation
times, progress age and recovery classification. DB-read failure returns HTTP 503
with explicitly local process diagnostics; no fresh control version or owner is
invented. `/live` measures a progressing service, while `/ready` measures trading
readiness. Collateral, exposure, instrument and feed waits have distinct reasons.

## Handover regression found during E0

The first new runtime gate also required the observer's `running` acknowledgement.
Full-image V1 standby handover showed why that was wrong: the reconciled strategy
could tick and submit its first quotes before the next observer cycle. Those
legitimate first orders were rejected; the deterministic long-refresh strategy
then remained without working quotes.

A failing native test reproduces this interval. The corrected gate checks task
health, while the existing ownership, reconciliation, control version, recorder
and session guards remain the authority to send. It does not use the later UI
acknowledgement as another trading permission. A separate Red/Green test also
fixes the misleading generic feed-wait reason when collateral is insufficient.

The post-cadence probe also exposed a separate switch race before its measured
window. Connectors start tracking queued creates before each create reaches the
durable send gate. Checkpointing one admitted intent previously copied all tracked
siblings, including siblings later rejected without ever sending. A rapid switch
could restore those nonexistent orders, receive an empty exchange response and
exit during required reconciliation. The failed attempt has no load measurements:
`build/mm-load/20261026-165826-ec4292/result.json`; its independent exchange ledger
contains zero accepted orders.

A real-PostgreSQL Red/Green test reproduces that phantom checkpoint. PostgreSQL
tracking checkpoints now include only identities present in the matching durable
order/config/market records. The query is bounded by current tracking IDs and uses
the existing order identity index. Existing preservation of unresolved persisted
intents remains intact; no uncertain remote order is declared absent. File-mode
checkpoint behavior is unchanged. Nineteen recorder unit tests and the real-PG
ownership/recorder suite cover this correction.

## Verification

| Check | Result |
|---|---|
| Native regression target | 1,361 tests and nine subtests passed, across four process groups: 149 / 11 / 959 / 242 |
| Final recorder correction | Nineteen recorder unit tests rerun after the checkpoint fix; overlaps native regression |
| Focused E0 tests with RuntimeWarning promoted to errors | 26 passed; these overlap the regression suite |
| Full disposable PostgreSQL suite | 134 passed |
| Cadence/savepoint/control PostgreSQL cases | Six passed, 127 deselected; includes real invalid-NUMERIC rollback and critical failure propagation |
| Final image | Fourteen runtime/contract hashes match image `sha256:1791cdc64d2117f587cee15303d9d9b98a6c89321653af23b6042d776f087695`; size 1,364,564,090 bytes |
| Full-image offline E2E | Seven groups passed: schema, V1 spot, V2 spot/perpetual, LIFE matrix, funding and availability |
| Short load probe | Sixty seconds passed: read p95 50.40ms / p99 232.05ms; 1,200 HTTP 200 responses, zero unexpected errors or budget violations |

The broad native suites emitted deprecation and mock-coroutine warnings; the
focused E0 warning-as-error run passed. Raw logs retain those warnings. No native
or PostgreSQL test above contacts a real OKX account or uses the local `.env`.

The image availability matrix injects three optional failures (report projection,
daily snapshot, reconciliation claim), transient read-only startup failure,
reference staleness and a failed controller task. It checks automatic recovery
with the same process, retained anchors/deadline and correct HALT for the failed
trading task. Full E2E additionally covers the existing V1/V2 lifecycle, nine LIFE
profile/market combinations, actual recorder rollback and DB/owner uncertainty.
Final-image artifact: `build/e2e-offline/20261026-170940-057693/result.json`.
The availability group recorded three recovered optional faults, startup and
stale-feed recovery, preserved anchors/deadline, zero unexpected process restarts
and durable HALT for the failed trading task. This run does not claim the optional
previous-image upgrade gate (`release_scope_complete` remains false).

An earlier harness attempt failed to import the application from its test mount;
the wrapper path was corrected and startup polling now fails promptly on an
exited container. The earlier V1 handover gate failure is also retained under
ignored `build/e0/`; neither failed attempt counts as acceptance.

## Load regression and correction

An initial sixty-second probe on the E0 image with a savepoint on every owner
publication failed unchanged latency budgets. A second untraced run confirmed the
regression; the intervening traced run passed. No request returned an unexpected
error in those runs. Controlled diagnostic overlays used the same compiled image
and dependencies: pre-E0 application code passed, and E0 with only the old
publication implementation passed. That isolated the new optional publication
path as the useful optimization target, without proving savepoint cost was the
only cause of every tail event.

| Run | Read p95 / p99 (ms) | Budget result |
|---|---|---|
| E0, savepoint each tick, untraced | 531.33 / 1,446.86 | Failed |
| Same image, traced | 113.97 / 567.82 | Passed |
| Same image, untraced control | 547.79 / 935.74 | Failed p95 |
| Pre-E0 application diagnostic overlay | 82.87 / 659.17 | Passed |
| E0 with old publication diagnostic overlay | 54.13 / 201.75 | Passed |

The correction keeps transaction isolation, schedules optional writes outside the
per-tick hot path, and adds Red/Green cases for cadence, currency/day changes,
fenced rejection and retained retry reasons. A PostgreSQL statement observer
verifies that skipped checkpoints issue no savepoint while truthful control
receipts still commit. The final full-build untraced probe passed unchanged budgets
at 19.97 completed reads/s, 99.98 market updates/s and one fill/s, with command
acceptance p95 16.83ms and observed RSS 324,644,864 bytes. All sixty independent
fills match recorder identities; positions match; two funding bills and three
daily balance checkpoints are present. This is one short diagnostic observation,
not a new sixty-minute certification or a claim that all tail variation is solved.
Artifact: `build/mm-load/20261026-172223-14ef7f/result.json`.

The full PostgreSQL run initially retained an eleven-statement recorder assertion.
The new checkpoint identity filter adds one indexed batch lookup, so that fixture
now expects twelve statements in both indexed/unindexed benchmark variants. The
corrected complete suite passed 134 tests; latency limits were not changed.

[Portable evidence](life-mm-e0-availability.json) records final source hashes,
immutable image identity, test scope, metrics and unsuccessful diagnostic controls.
Raw logs remain under ignored `build/e0/`, `build/e2e-offline/` and `build/mm-load/`.

## Operational limits

- Read-only initialization and normal feed reconnects can recover automatically.
  Failed/stalled trading tasks, recorder failure, lost ownership, unresolved
  exchange mutations and unclean exits still require conservative recovery.
- Availability means a usable management process during classified observation
  failures; it does not mean quoting through unknown balances, missing required
  prices or exceeded limits. No automatic HALT reset was added.
- The local simulator verifies transport/state consistency, not strategy
  profitability or production exchange fill quality. The short probe does not
  replace final mixed/burst/sixty-minute acceptance for the later enhancements.
- E1 and subsequent phases, including listing bootstrap and additional risk
  policies, have not started. This delivery introduces no additional service,
  external cache, Cloud Run deployment or real OKX Demo operation.

Operational contracts are in the [runtime guide](../docs/postgres-runtime.md),
[admin API guide](../docs/admin-api.md) and
[local test guide](../docs/life-mm-load-tests.md).
