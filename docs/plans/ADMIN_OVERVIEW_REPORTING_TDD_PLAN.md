# Admin Overview reporting — TDD remediation

Updated 2026-11-16 local. Status: contract/coverage/fees implemented; a bounded
writer-read follow-up passed functional regression but fails resource load. An
encoding/authority-lock follow-up has full native and PostgreSQL GREEN.
Resource qualification remains open for failed warmup latency and one paired
cancel-p99 comparison. The final full 60-minute soak and all six fault windows pass.

Implementation is on `codex/overview-reporting-tdd`; PR #2 is merged into the
working tree; qualification measurements were collected before commit. A/B/D and
focused isolation tests are implemented.
Final native regression passes 4,033 tests +16 subtests; FE lint/typecheck/610 tests/
build and all 989 cases across 115 PG modules pass on the latest source. The
earlier writer-source run retains two stale SQL test matcher failures and their
explicit passing follow-ups. After the publication-read change, all 115 affected
PG cases in 27 modules also pass.
Two pre-existing Stop races found in baseline preparation were reproduced and
fixed without weakening proof; both comparison artifacts have the same fixes.
Packaged terminal proof passed before the latest read projection. First full-image
paired/soak/fault qualification failed. The new candidate's lifecycle checks pass,
but resource gates still fail. One long run is interrupted for unconfirmed power;
AC is later verified; that scope also fails relative/burst latency gates. A
writer-read follow-up has targeted/native and full PG GREEN; a new frozen image
passes packaged lifecycle and SQL isolation but fails paired latency and mixed
load. Encoding large status JSON inside its authority lock has a controlled RED
reproduction; the new fix passes all 989 PG cases and 4,033 native tests +16
subtests, scoped packaged lifecycle, full soak and SQL isolation. All six 1GiB
component windows pass absolute budgets; one cancel-p99 comparison and canonical
warmup still fail. C remains open with unchanged thresholds.
Evidence and failure scopes are consolidated in
[the report](../../reports/performance/admin-overview-reporting.md).

## Scope and baseline

Implement the four follow-ups approved after reviewing
[PR #2](https://github.com/lifenetwork-ai/hummingbot/pull/2): correct the Overview
contract, add eligible rolling coverage, qualify reporting isolation under load,
and remove the permanent 10,000-fill limit for long-running session fees.

Reviewed PR head: `c17c938670dd0ea1543467e6b38db20d5331a970`.
Local dev baseline: `e9549a6cd62972653be6844f433db462aa8f60b9`.
The Overview modules named below belong to the PR; they are not yet on this dev
baseline. Implement against a branch that incorporates the reviewed work and the
current dev fixes; retain the report-readiness barrier in `strategy_v2_base.py`.
Creating this plan does not merge the PR, deploy, change policy or reset HALT.

Review evidence: 85 focused PR tests passed. Three separate counterexamples failed:
BTC inventory becomes unavailable, BTC inventory risk is labelled LIFE, and a
fee of `0.00000001` serializes as `"1E-8"`. These are review reproductions, not fixes.
The local tests are retained under `build/pr-2-api-review-20261116/`.

Use the existing `/api/v1/admin/status` supplement and generated OpenAPI pipeline.
No endpoint per UI card. Capital mandate, ROI, transfer-adjusted delta and hold
benchmark remain outside this implementation until their sources are defined.
Unknown/partial data remains explicit. No trading authority or risk limit changes.

## TDD workflow and delivery order

For each case, preserve a failing test on the unmodified implementation, the
specific failure, the passing implementation and relevant negative controls.
Do not replace an assertion with a weaker expectation to obtain GREEN.

Order: A contract fixes → B rolling classification → D fee projection → C final
isolation/load qualification. Establish C's baseline and fault tests early, then
rerun them on the final combined artifact. Commit-sized stages are independent;
passing an early stage does not qualify the final image.

- [x] Incorporate the PR on an implementation branch without dropping current dev fixes.
- [x] Freeze baseline source/fixture hashes and retain original RED evidence.
- [x] Complete A, B and D with focused regression tests.
- [ ] Complete C against the final source and immutable image.
- [x] Generate contracts, update FE documentation and record acceptance by scope.

## A. Correct asset units and decimal serialization

Targets: `hummingbot/cli/admin_overview_models.py`, `admin_overview.py`, existing
market capture, and their contract/projection/HTTP tests.

### RED

- [x] Run the three retained counterexamples against the actual PR modules.
- [x] Parameterize Spot/perpetual, BTC/LIFE and another valid configured base asset:
  `inventory_limits` remains available and each risk quantity carries its actual
  asset. Long, short and gross caps retain their distinct meanings.
- [x] Reject or mark a mismatched asset/pair unavailable; changing the schema to
  unrestricted strings must not admit contradictory units.
- [x] Verify wire serialization for small fees/rebates, tiny quantities, zero,
  large values and significant decimals through the real HTTP response. Require
  plain decimal strings and exact values; reject non-finite values.
- [x] Check the generated schema and existing FE decimal arithmetic against those
  payloads. A serializer-only test is insufficient.

### GREEN and acceptance

- [x] Derive inventory asset from the configured pair; represent quantity units
  explicitly, including in risk summaries. Keep USDT/bps/time units distinct.
- [x] Apply a shared plain-decimal serializer to the Overview financial types;
  avoid float conversion or UI-specific rounding.
- [x] Regenerate OpenAPI and TypeScript types; no handwritten generated edits.
- [x] Add counterexamples to normal regression. Existing unknown/freshness and
  partial-metric isolation tests still pass; no exchange request is introduced.

## B. Eligible two-sided coverage over a rolling 30-minute window

Targets: `mm_liquidity.py`, `overview_capture.py`, Overview coverage models,
projection and existing quote diagnostics. Reuse resident observations; do not
add exchange polling or call an admission helper with side effects.

### Measurement contract before implementation

Preserve `rolling_30m_fraction = two_sided_seconds / elapsed_seconds` as the
all-time view. Add an independent eligibility classification; the existing
controller-readiness `eligible_seconds` counter is not the approved SLO.

- [x] An eligible interval requires an admitted running session, fresh evidence
  that both sides are funded, and no explicit current guard/inventory/bootstrap
  restriction. Normal replacement/pending ACK does not excuse a missing quote.
- [x] Classify intervals as eligible, explicitly restricted with a reason, or
  eligibility-unknown. Missing/stale/inconsistent evidence is unknown.
- [x] Add `eligible_seconds`, `two_sided_eligible_seconds`, `restricted_seconds`,
  a bounded restriction-reason breakdown and `eligibility_unknown_seconds`, with
  matching per-minute fields. These eligibility categories partition elapsed
  time; retain existing observation `unknown_seconds` with its original meaning.
- [x] Publish the conditional eligible fraction for diagnostics, plus an explicit
  conservative fraction `two_sided_eligible_seconds /
  (eligible_seconds + eligibility_unknown_seconds)`. Document both denominators;
  unknown never becomes successful quoting. A zero eligible denominator is
  unqualified, not 100%. Do not overwrite the old all-time fraction.
- [x] Keep `full_window`, original observation timestamps, market, session and
  generation scope. Publish measurement readiness/reason separately from the
  numeric result. Missing target configuration remains null; never infer a target
  from the data. The approved 0.99 target is a qualification input, not enforcement.

### RED matrix

- [x] A full window with 900s restricted and 900s eligible/two-sided yields 50%
  all-time and 100% conditional/conservative eligible coverage. Restriction stays visible.
- [x] A window with 900s eligible/two-sided and 900s eligibility-unknown yields
  100% conditional and 50% conservative coverage; it cannot pass the 99% target.
- [x] Ordinary refresh, pending create/cancel and delayed ACK remain eligible
  when all eligibility evidence is fresh; missing quotes reduce the numerator.
- [x] Test one-sided funding, long/short/gross caps, quote guard, bootstrap,
  missing guard evidence and stale private/public data independently.
- [x] Test exact window/minute boundaries, partial windows, duplicate timestamps,
  clock reversal, monotonic/wall divergence, owner takeover, restart, generation
  change, Random step, Stop/end clipping and legacy checkpoints without history.
- [x] Test high-frequency alternating states over a full 1,800 seconds. A bounded
  history must not silently lose coverage or turn dropped intervals into success.
- [x] Assert partition/sum invariants and `two_sided_eligible <= eligible`; verify
  API/FE distinguish total-time coverage, conditional coverage and conservative SLO.

### GREEN and acceptance

- [x] Store bounded interval/bucket history containing the classification and
  evidence needed for the approved definition; do not reconstruct it from
  cumulative counters or classify a whole interval from only its final sample.
- [x] Clip transitions conservatively and preserve unknown gaps across changes.
  Bound memory/checkpoint size and retain private history only server-side.
- [x] Use an independent reference timeline in tests for randomized transitions,
  rather than recomputing expected values with the implementation helper.
- [x] Update OpenAPI/types and `docs/fe-integration.md`: exact formulas, reason
  codes, null behavior, sampled measurement limitations and SLO readiness.

## D. Session fee reporting beyond 10,000 fills

Targets: `hummingbot/model/overview_fees.py`, `overview_fee_cache.py`, admin DB
scheduling and new disposable-PostgreSQL projection tests. This stage may require
an additive migration; select its number from the implementation branch.

### Design to test

- [x] Keep the current proven bot/session/account/environment attribution and
  conversion-at-fill rules. Source account binding comes from the submission
  context, not the connector's current key. Handle generations of one session
  without leaking another session or rotated account into its total.
- [x] Build a reporting-only durable contribution projection, unique by the
  source fill identity plus projection/scope version. Each accepted contribution
  updates its aggregate/counts atomically; replay must have no additional effect.
- [x] Process bounded batches on the existing heavy DB lane with single-flight,
  backpressure, statement/lock deadlines and a yield between batches. No extra
  process/pool and no new dependency in order admission, fill commit or send.
- [x] Discover unprocessed source identities with indexed, bounded catch-up and
  reconciliation. A trade timestamp or sequence high-water mark alone is unsafe:
  delayed fills and transactions committing out of sequence must still be found.
- [x] Aggregate exact Decimal contributions and conversion/attribution failures.
  Separate processing catch-up from financial history completeness. Finishing a
  backfill cannot turn unprovable legacy history into `history_complete=true`.
- [x] Serve the last completed aggregate while catching up, with `processing_state`,
  `processed_fill_count`, processing observation time and `caught_up_at` (nullable).
  Define `through_at` precisely; maximum trade timestamp is not proof of coverage.
  Use `catching_up`/`unavailable` plus reason when no usable aggregate exists.
- [x] Document startup, no-poll progress, stopped-session reads, retention and
  projection-version rebuild behavior. Published totals must remain scoped and
  readable across restart; no full-session scan on every status request.

### RED matrix on real disposable PostgreSQL

- [x] 9,999 / 10,000 / 10,001 / 100,000 attributed fills in one until-stopped
  session produce the same exact total as an independent full-history oracle;
  there is no permanent row-limit unavailable state.
- [x] New fills continue during catch-up; a late fill has an older trade timestamp.
  Include equal timestamps, sequence allocation before delayed commit, and a
  bounded set of fills whose attribution becomes provable after first discovery.
- [x] Duplicate delivery, concurrent projection workers and repeated batch replay
  contribute each source fill once. Test different identities sharing timestamps.
- [x] Kill a worker after reading sources, after inserting contributions, after
  aggregate update/before COMMIT, and after server COMMIT/before acknowledgment.
  Resume/retry converges with no missing or doubled fees using actual transactions.
- [x] Inject lock timeout, SQL timeout, DB disconnect and process restart; keep
  the last usable result labelled correctly and allow subsequent catch-up.
- [x] Isolate bot/session/account/Demo/Live scope, account rotation, zero fees,
  rebates, base-token fees and unknown third-token conversion. Never turn an
  unprovable amount into zero or include it in a supposedly complete total.
- [x] Test legacy/empty history, additive migration from the current schema,
  repeated startup and previous-image compatibility. Restore/rollback behavior
  must be explicit if a previous binary cannot read the new projection.
- [x] Exercise `ENABLE_AUTO_MIGRATION=true` with an active owner and overlapping
  revisions. New reporting tables/indexes must not invalidate financial schema
  checks, hold an unbounded runtime lock or require backfill inside startup.
  Run destructive fault/migration tests only against disposable databases.
- [x] Check query plans/rows examined at 100k and 1m retained source fills, with
  multiple scopes. Bound per-batch and warm-read work, including anti-join discovery;
  a LIMIT on returned rows is not evidence of bounded database work.

### GREEN and acceptance

- [x] Keep contributions, progress and aggregate in one transactional protocol;
  idempotency survives unknown COMMIT outcomes. Existing financial rows, journal
  applied flags, positions and risk checkpoints are not repurposed or rewritten.
- [x] Status reads remain bounded as session history grows; background catch-up
  continues without UI polling and yields capacity to other heavy reports.
- [x] Publish a rebuilt projection version atomically after verification; retain
  the last usable version during catch-up. Recheck cache freshness on each read,
  including stopped sessions and restart, without hiding its partial-history label.
- [x] Document partial totals/reasons and update generated contracts. Migration
  and rollback checks pass before using the new image on connected Demo.

## C. Reporting isolation and resource qualification

Reuse `scripts/test_mm_load.py`, `scripts/test_mm_reporting_isolation.py`,
`scripts/mm_load_budgets.py` and the offline exchange/service/client fixtures.
Extend the harness for Overview rather than replace the existing workloads.

### RED / fault coverage

- [x] Hold the actual Overview fee query/lock while concurrently polling status,
  issuing Stop and delivering exchange events/fills. Test cold-cache, concurrent
  readers, cancellation, timeout/recovery and shutdown with a physical worker busy.
- [x] Assert `/status` uses bounded cached/pending reporting data; it does not wait
  for fee backfill to complete. Measure cold-cache behavior, including the PR's
  existing optional 200ms wait, rather than hiding it outside request timing.
- [x] Fill the 30-minute coverage/recovery history to its supported bound; test
  cold construction, serialization, checkpoint publication and restored history.
- [x] Fill reporting queues and slow DB work; prove operational/control capacity
  remains reserved. A cancelled client cannot falsely release a busy worker slot.
- [x] Inject collector/projection failures without manufacturing trading faults:
  metrics become unavailable, while readiness/ownership/send gates retain their
  existing behavior. Do not suppress genuine HALT/ownership failures to pass.

### Paired load and acceptance

- [x] Compare a frozen baseline and candidate using identical fixtures, CPU,
  memory, history size, rates, guards, durations and warmup; repeat the paired
  component window three times. Report absolute results and deltas, including
  queue wait, worker time, event-loop delay, tick progress, ACK/cancel timing,
  snapshot bytes, SQL work, throughput and retained memory.
- [x] Keep existing gates unchanged: read p95 <=500ms/p99 <=1,000ms, command accept
  p95 <=500ms/p99 <=1,000ms, Start/Switch <=15s, Stop <=5s, and existing load rates,
  backlog, error, financial integrity and retention limits. Healthy reporting
  faults must not cause a restart/HALT or loss of trading progress.
  Retain the final warmup failure; unchanged thresholds do not imply acceptance.
- [x] Run a separate production-shaped 2 CPU / 1,024MiB cgroup qualification and
  retain its hard memory limit; the historical 1.5GiB RSS allowance cannot stand
  in for success under a 1GiB deployment. Keep the canonical load results separate.
  All six 120-second windows pass absolute budgets; a paired cancel-p99 failure
  still prevents C acceptance. This does not qualify a full hour under 1GiB.
- [x] Before collecting results, freeze additional resource-regression budgets:
  candidate tick/ACK/cancel p99 must not regress beyond a declared noise margin;
  start with max(10% of baseline, 10ms), measured on identical paired windows.
  This is a proposed local regression gate, not a new exchange latency promise.
  Predeclare any adjustment from baseline variability, never after candidate failure.
- [x] Complete unchanged warmup/mixed/burst, Random soak 3,600s and existing fault
  windows on the final immutable image; include Overview refresh/catch-up. Retain
  every slow probe and failure with its interval and evidence. Do not classify a
  reporting probe as proof that MM stopped, or use fills alone as proof of quotes.
  Execution is complete: mixed/burst/soak and all six faults pass, warmup fails.

## Verification and closeout checklist

Final encoding-source measurements complete with unchanged budgets. All six
1GiB component windows pass absolute budgets and all tick/gap/submit comparisons
pass, while pair two cancel p99 fails (41.69 →125.46ms; allowed increase 10ms).
The 2GiB canonical mixed/burst/full 3,600-second soak and all six faults pass;
warmup read p95/p99 fails at 2,983.73/5,127.91ms. C remains unqualified. Checkboxes
above record execution and preserved thresholds, not a waived acceptance failure.
Final SQL isolation passes two real 57014 timeouts with a committed-fill witness,
stable owner/session/PID, stale-to-fresh fee recovery and clean Stop. Every earlier
failed/interrupted image scope remains in the report; none is rewritten as passed.

- [x] A: focused model/projection/HTTP tests and existing Overview regressions.
- [x] B: timeline/reference tests, continuity/freshness tests, API contract and
  FE decimal/coverage integration checks using generated types.
- [x] D: disposable PostgreSQL integration, migration, retry/crash/unknown-COMMIT
  and large-history query-plan tests; never point destructive fixtures at Demo DB.
- [ ] C: native readiness/report/owner regression, packaged offline lifecycle,
  load/isolation, full soak and unchanged financial-ledger checks.
- [x] Run `make swagger` and the existing schema generation/check pipeline, then
  dashboard lint/typecheck/unit/build checks. Reuse existing browser tests; any
  additional UI acceptance is a separately scoped implementation task.
- [x] Record tested commit, image digest, source/fixture hashes, resource settings,
  commands, RED/GREEN receipts and remaining limits in one scoped release report.
- [ ] After deployment, verify the exact image and one uninterrupted 30-minute
  Demo window. Compare API values with independent fees/classified timelines and
  inspect probes, engine/quote progress, Stop capability and memory. This plan
  does not authorize resetting risk state or relaxing limits to obtain samples.
- [ ] Close code/API gates separately from the 99% measured quote target. Correct
  coverage calculation does not imply the bot actually achieves that target;
  no eligible time remains unqualified, and unknown/failed windows stay recorded.

Recommended first implementation: A, preserving the three review REDs; then build
B's eligibility timeline fixtures and D's durable replay tests before production edits.

## Local qualification notes (2026-11-16)

- Worktree source includes the PR plus the current dev report-readiness barrier.
  No trading/risk/recovery policy changes are part of this implementation.
- RED/GREEN evidence is retained under `build/overview-reporting-tdd-20261116/`.
  HTTP decimals include tiny positive/negative, zero, large and significant values.
- Eligibility uses admitted session/generation proof from the real controller.
  A boundary between 900 restricted and 900 eligible seconds conservatively
  leaves the one transition second unknown (899 proven eligible); it is not
  credited from a single endpoint. High-churn reference and malformed-checkpoint
  tests preserve partition and memory bounds.
- The first broad PG run was interrupted: 7 failed /723 passed. Preserve it.
  The quote endpoint test must isolate periodic background discovery for its
  strict one-query assertion. The existing native cold-recovery fixture must
  include explicit opposite-leg absence proof, as the unchanged core requires.
  These corrected fixtures pass in isolation. The pending-intent benchmark also
  passes in isolation; its broad failure was a DB idle-in-transaction disconnect
  during explicit Python GC, not a changed SQL budget. Full-module qualification
  uses fresh-process groups with the existing disposable DB runner and unchanged
  pytest/DB timeouts; do not describe the interrupted monolithic run as passing.
- A further physical-plan RED with partial fills exposes 3,000 source rows read
  for 501 returned rows. The replacement seeks the order-leading source index
  directly; final plan/replay checks pass with 501 source visits.
- Cache freshness has separate RED/GREEN cases: each read rechecks the DB clock,
  and a busy refresh retains a usable total labelled stale with its original time.

Final regression update: all 114 PG modules completed; the original 964 cases plus
two projection cases, seven readback-race cases and one coupled tracker/shutdown
case cover 974 distinct cases. The final source native regression passes 4,033
tests +16 subtests. Preserve the monolithic and group-4 failure logs rather than
overwriting them with the rerun. The packaged lifecycle has scoped rerun evidence;
it is not a clean full-suite run on the final image. Paired resource/latency windows
and full soak remain open.


### Qualification follow-up: concurrent create rejection during Stop

- [x] Preserve the baseline preparation failure before any measured window.
- [x] Reproduce final readback racing a proven-unsent native rejection (RED), with
  sent/acknowledged/replaced/nonterminal/unproven and unrelated-error controls.
- [x] Recheck the existing evidence at the actual read boundary (GREEN); retain
  strict preparation and shutdown/financial fences.
- [x] Reproduce queued PENDING_CANCEL after FAILED/CANCELED/FILLED reopening a
  terminal tracker order; ignore only that late state ACK, preserving newly
  received exchange IDs and normal late-fill/deduplication handling.
- [x] Couple the real tracker transition with owned PostgreSQL shutdown evidence;
  rerun all 84 related shutdown/control cases.
- [x] Reject unexpected setup restarts before collecting a healthy load window.
- [x] Apply both identical runtime fixes to baseline and candidate.
- [x] Requalify packaged terminal proof on the final immutable image.
- [x] Execute paired/load checks on the final encoding digest; preserve the
  warmup and paired cancel-p99 failures in C rather than marking acceptance.

### Qualification follow-up: private histories in hot reporting reads

- [x] Preserve the full one-hour soak failure, snapshot sizes, exact ledger and
  later fault-window failures; do not replace them with short-window results.
- [x] Reproduce avoidable transfer through the actual DBAPI JSON decoder: a
  728,488-byte private checkpoint crosses the publication boundary (RED), with
  seven missing/null/scalar/legacy controls already passing.
- [x] Filter only the three known private liquidity histories in the existing
  publication SELECT, before transfer/decoding. Preserve one query, identical
  public values, full owner/restart reads and exact durable checkpoint contents.
- [x] Verify all eight boundary cases and the existing real-SQL HTTP/Stop/owner
  and quote cases (12 PG cases); retain the intermediate SQL-precedence failure.
- [x] Rerun native regression on the changed source (4,033 +16 subtests).
- [x] Rerun PostgreSQL publication consumers and verify fresh control/authority
  (115 cases in 27 modules, all pass).
- [x] Freeze a separate candidate; verify its 1,133 packaged file hashes and
  preserve the earlier failed artifacts and measurements.
- [x] Repeat packaged lifecycle and unchanged paired/isolation/canonical gates,
  including a full 3,600-second soak. Known byte-work reduction does not prove a
  unique cause for every earlier p99 spike.
  Final encoding-source replay includes this read filter; full soak completes,
  with warmup/cancel acceptance failures retained separately in C.

Packaged lifecycle (shutdown proof, managed lifecycle and upgrade/rollback) and
the new two-timeout SQL isolation pass. The second six-window comparison still
fails absolute read budgets and two submit-p99 comparisons. Host inspection then
reports Battery Power / hardware `ExternalConnected=No`; transition times for
earlier windows are unknown, so this does not establish battery causation. The
unfinished new canonical run is deliberately interrupted and cleaned up, not
reported as a passed soak or an application failure. Confirm stable external
power before another scoped resource run; preserve all completed failed windows
and unchanged acceptance margins. AC is subsequently verified through `pmset`
and hardware `ExternalConnected=Yes`. That monitored scope passes six absolute
component windows but fails three relative comparisons and burst latency. Its
remaining partial soak is interrupted for the writer follow-up below. All failed
and interrupted results stay retained; C remains open.

### Qualification follow-up: previous-status work in the publisher

- [x] Complete the predeclared identical-baseline A/A control: four absolute
  windows and both relative comparisons pass. This does not waive failed A/B gates.
- [x] Audit hot readers: the owner authority query is already thin; full private
  checkpoint reads are still required at observer restoration. Avoid broad changes.
- [x] Reproduce the publisher's unnecessary old-checkpoint decode: two REDs at
  729,035 bytes, with 11 other controls passing and transition/fence checks intact.
- [x] Read only the old guard observation when status is an object; preserve
  legacy shapes, the full session, row lock and all authority/version predicates.
  Continue writing the complete new checkpoint, heartbeat and command outcome.
- [x] Targeted PG GREEN (21), including repeated/concurrent transition accounting,
  stale authority rejection, full private state and actual-SQL isolation.
- [x] Full native regression GREEN (4,033 +16 subtests).
- [x] Full PG regression in fresh groups on this publisher source: 987 distinct
  cases across 115 modules. Preserve two stale SELECT-matcher failures and their
  successful contention (3) / availability (10) follow-ups, with unchanged bounds.
- [x] Freeze the writer candidate and replay scoped packaged/component/isolation/
  load checks. Its mixed run fails before soak; the cumulative encoding follow-up
  below completes the full 3,600-second soak without rewriting that failed run.

### Qualification follow-up: encoding outside the authority lock

- [x] Preserve the writer candidate's five absolute component passes, one
  auxiliary-read failure, five relative failures and successful SQL isolation.
- [x] Preserve mixed-load 55P03, engine restart/HALT and independent fill mismatch;
  mark burst/soak not reached. The later crypto error follows intentional provider
  outage. Do not claim a known historical blocker or unique latency cause.
- [x] Controlled RED: large-payload JSON adaptation holds the authority row lock
  and blocks the actual financial fence and Stop; 2 RED /13 other cases passing.
- [x] Encode before the transaction; merge guard/error metadata under the same
  fenced lock and retain one atomic checkpoint/ACK/event transaction.
- [x] Focused GREEN (30): concurrent Stop invalidates the prepared publication;
  financial fence proceeds, full checkpoint and repeated guard sequence survive.
- [x] Full native (4,033 +16 subtests) and all-module PG regression (989 cases in
  115 modules /seven fresh-process groups, no failures) on the encoding change.
- [x] Freeze a distinct artifact and execute unchanged packaged/resource gates:
  1,133 file hashes match; three packaged scopes, full 3,600-second soak, six faults
  and SQL isolation pass. Keep warmup and one paired cancel-p99 failure open.

Remaining C work: trace the broad API warmup tails and paired cancel outlier with
bounded evidence, reproduce any demonstrated code fault, and retain the existing
acceptance margins. Do not infer a unique cause from the known encoding-lock fix,
or use another passing window to replace a failed one. Connected 30-minute Demo
acceptance remains separate after an approved deployment.

### C investigation follow-up — 2026-11-17

- [x] Retain the failed pair-two cancel interval: the slowest call takes
  125.459ms, with the simulator handler/terminal reached at 15.550/15.602ms;
  another 109.856ms elapses before the native coroutine returns. Other calls in
  the same twelve-cancel burst also show tails. This does not uniquely attribute
  time between HTTP handler completion and coroutine return.
- [x] Locate the warmup transition: minute samples through 240s retain roughly
  131–134ms p95; the last minute produces the broad route tails. The original
  canonical run did not retain causal incident stacks at that boundary.
- [x] Add bounded deferred capture for completed cancels >=100ms in the opt-in
  MM diagnostic wrapper. Producer callbacks only enqueue scalar markers; the
  watchdog flushes incident history. Three new RED/GREEN cases and 59 nearby
  diagnostic tests pass, including lock reentry, capacity and private-field checks.
- [x] Run one predeclared diagnostic pair, candidate then baseline: 360 seconds
  each, 2 CPU /2GiB, 100,000 history rows, the same normalized healthy depth,
  security/defense workload and immutable production images. Enable GC timing
  and bounded incident/MM capture (128 incident limit, unchanged RAM/disk caps)
  to cover the late warmup transition and slow cancel bursts. Observe AC before,
  during and after. These are diagnosis, not replacement acceptance results.
  Candidate p95/p99 135.13/420.56ms, baseline 51.49/186.30ms; maximum native
  cancel 47.75/60.74ms over 67 samples each. Neither original tail is reproduced.
  API full GC events remain <=18.20ms; this does not attribute the old 3s stall.
- [x] Add full-trace async cancel spans with nested SQL/queue context; three
  RED/GREEN result/exception/cancellation cases preserve inputs and outcomes.
  All 62 nearby diagnostic tests pass. The failed RED test process starts after
  the baseline measurement ends, so it does not overlap the measured interval.
- [x] Replay exactly the first canonical 300-second warmup with full tracing and
  GC timing, then stop before mixed. Keep production images/resources/workload
  unchanged; this bounded diagnosis also does not replace failed acceptance.
  `20261117-003452-8138e6`: warmup p95/p99 133.65/324.35ms, no HTTP errors;
  original multi-second tail not reproduced. The largest retained API request
  is 673.06ms, including two sequential SQL spans of 378.87/286.96ms; sampled
  API full GC remains <=16.03ms. No new production-path fix is justified by this
  diagnostic. The deliberately partial run does not qualify the full release.
  Final expanded diagnostic regression: 81 passed; all changed-file hooks pass.
- [ ] Attribute demonstrated stalls, reproduce actual code faults with tests,
  and apply the smallest fix. Preserve failed gates and acceptance margins.
- [ ] Repeat affected checks and qualify the final runtime artifact, including
  the required unchanged load/soak scope after any production-path change.
