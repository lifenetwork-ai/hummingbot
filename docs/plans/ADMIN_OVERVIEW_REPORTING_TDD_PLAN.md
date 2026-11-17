# Admin Overview reporting — TDD remediation

Updated 2026-11-17 local. Status: contract/coverage/fees implemented; a bounded
writer-read follow-up passed functional regression but fails resource load. An
encoding/authority-lock follow-up has full native and PostgreSQL GREEN.
Resource qualification remains open for the retained warmup/cancel failures and
the latest paired submit-p99 failure. The historical full 60-minute soak and all
six fault windows pass; the new local requalification stops after its first pair.

Current source adds bounded final-send priority and cooperative outflow clock
catch-up. The new 1,134-file artifact passes 4,074 native tests +16 subtests and
95 focused checks, all 250 affected PG cases and scoped packaged lifecycle.
Its first resource pair fails only cancel p99; remaining pairs and canonical
soak are not started. New-image real-SQL reporting isolation also passes.
The preceding priority artifact retains a failed tick-gap/cancel pair. Historical
artifact passes do not qualify this combined source.

Implementation is on `codex/overview-reporting-tdd`; PR #2 is merged into the
working tree; qualification measurements were collected before commit. A/B/D and
focused isolation tests are implemented.
The preceding encoding artifact passes 4,033 native tests +16 subtests, FE
lint/typecheck/610 tests/build and all 989 cases across 115 PG modules. The
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

### Controlled CPU contention diagnosis — predeclared 2026-11-17

This experiment tests sensitivity to CPU contention; it cannot establish the
cause of a historical event or replace healthy acceptance. No quotas, deadlines,
guards, risk settings or production images will be changed.

- [x] Run baseline then candidate, once each: 360-second diagnostic probe,
  2 CPU /2GiB application quota, 100,000 history rows, the same healthy book,
  security/defense workload, GC/incident/MM capture and 128 incident limit.
- [x] Within each probe inject the same bounded sibling-process CPU pulses in
  the application cgroup: three workers for 20 seconds at approximately 180s,
  then twelve workers for 10 seconds at approximately 260s. Each worker has an
  absolute deadline; record actual container timestamps and cgroup counters.
  The last approximately 90 seconds observe recovery. Stop only owned workers.
- [x] Retain failed latency/rate gates during injection; inspect independent
  ledger convergence, process/owner continuity and recovery after the pulses.
  Record AC and Docker VM resources. Do not run regressions during measurement.
- [x] Compare incident stacks, heartbeat/CPU counters and native cancel/venue
  terminal timestamps. If a code fault is demonstrated, reproduce it with a
  failing test before fixing it. Otherwise report the remaining uncertainty.

Results: baseline `20261117-005346-0ff3d1`, candidate
`20261117-010136-69bc4f`. Read p95/p99 is 519.34/5,024.63ms versus
3,567.82/6,252.99ms; both diagnostic runs retain latency failures. Each has 360
exact independent fills, matching positions/funding, stable sampled role PIDs,
no restart/error logs and a final running state with working BUY/SELL quotes.
Baseline's 162.81ms cancel overlaps the first CPU pulse; candidate's largest
181.93ms cancel occurs before the injected worker pulse. CPU contention can
produce the observed symptoms, but it is not a unique explanation of the old
failures or of every new cancel tail.

The pre-pulse candidate trace also records a generation-zero GC pause of
726.82ms and API heartbeat lag of 836.30ms. Its GC wall-time measurement lacks
collector-thread CPU, so it cannot separate collection work from descheduling.
Three new RED/GREEN cases require both wall and thread CPU timing for generations
0/1/2; final diagnostic regression passes 84 cases. This test-only instrumentation
change follows measurement, so it supplies no retroactive CPU reading for that
pause. Production files, GC policy and thresholds remain unchanged. C stays open.

### Young-GC attribution follow-up — predeclared 2026-11-17

- [x] Run exactly one 300-second canonical warmup on the same frozen candidate,
  2 CPU /2GiB, 100,000 history rows and existing security/defense/book workload.
  Enable full tracing, deferred GC wall/thread CPU timing for all generations,
  full-GC timing and async cancel spans. Stop before mixed. Do not inject CPU
  workers or run test suites during this measurement; observe AC throughout.
- [x] Inspect GC pause intervals against collector-thread CPU, heartbeat lag,
  cgroup counters and cancel spans/SQL children. Elapsed time exceeding thread
  CPU is not automatically scheduler delay: it can also include waits or work
  elsewhere. Do not infer the old pause's CPU from a new collection.
- [x] Check independent fills/positions and final runtime state. Preserve all
  original acceptance failures. A passing diagnostic is not release qualification.
- [x] If a specific production fault is demonstrated, write its failing test
  before applying a fix. Otherwise retain the uncertainty and stop this bounded
  experiment without tuning GC or changing trading deadlines/guards.

Result `20261117-013436-448f3f`: read p95/p99 136.76/381.47ms; no warmup
violations or unexpected HTTP errors, 300 exact fills and matching positions,
stable role PIDs and final running/working-quote snapshots. API GC >=10ms is
captured only for generation two (14 events); largest wall/thread CPU is
13.742/13.289ms. No generation-zero/one pause >=10ms appears in the measured
window. The old 726.82ms API pause does not recur, so its cause remains unknown.
Engine full GC reaches 233.175ms wall /232.592ms thread CPU, demonstrating real
collector CPU in this new event, not explaining the historical API event.
No retained slow native cancel span appears. In-window capture metrics show
zero dropped GC events/records; engine child spans have bounded eviction, so
absence of a retained cancel span is not a new all-call latency qualification.
All 41 power observations report AC. Host CPU sampling includes busy editor/
browser processes; this remains a nonexclusive local diagnostic environment.
Stop the bounded experiment with no production change. C and connected Demo
acceptance remain open; this passing partial warmup supersedes no failed gate.

### Local requalification after operator idle confirmation — 2026-11-17

The operator confirms heavy tasks have stopped and the machine is ready. AC is
verified. Preserve all historical failed/diagnostic runs; write distinct artifacts.

- [x] Verify frozen baseline/candidate image digests and candidate production files, and Docker VM
  resources. Record source/fixture hashes and host power/process CPU samples.
  No concurrent test/build jobs, CPU injection, policy changes or new image builds.
- [x] Execute the fixed pair schedule with predeclared early stop: up to three
  complete sequential baseline/candidate pairs, 120 seconds
  per window, 2 CPU /1,024MiB, identical 100,000-row history, normalized healthy
  depth, security/defense workload and bounded incident/MM/GC diagnostics. Keep
  unchanged absolute gates and empirical p99 margin max(10% baseline, 10ms).
  Preserve all sample counts. Stop at the first failed pair to inspect evidence;
  missing/setup/fixture/power-interrupted scopes do not count as passes. No
  replacement pair or margin adjustment after a failure.
- [ ] Only if all three pairs pass, execute the unchanged candidate canonical
  warmup/mixed/burst/3,600-second Random soak and six faults at 2 CPU /2GiB,
  preserving failed windows and checking independent fills/fees/positions,
  process/owner continuity, reporting isolation and memory retention. A partial
  component pass cannot qualify the full release.
  Keep bounded incident/GC capture enabled on this new canonical run; MM activity
  remains probe-only. Record the diagnostic overhead instead of hiding it.
- [x] Record the result and resource caveats. Operator idle confirmation and
  process CPU samples do not turn a shared laptop into an exclusive CPU worker;
  retain any contention and do not infer its absence from AC or a passing run.

Only pair one completes: baseline `20261117-102533-c94415` and candidate
`20261117-102935-b045a0`. Both pass absolute budgets and have 120 exact fills,
matching positions/funding, no error/restart logs and peak cgroup memory below
1GiB. Read p95/p99 is 49.77/102.15ms versus 132.59/214.39ms. Tick, tick gap and
cancel comparisons pass. Submit p99 fails: 292.489 ->331.789ms, +39.299ms against
the unchanged 29.249ms margin. Native submit sample counts are 16/26; both p99
values are empirical maxima. Keep the failure; pairs two/three and canonical
load/soak are not started, as predeclared.

The worst candidate submit reaches simulator acceptance after 305.200ms, then
returns another 26.589ms later. No causal incident stacks cover that interval.
Inspection finds a test-harness gap: only slow cancels trigger deferred capture.
One new RED/GREEN case now retains a slow submit's preceding authorization/SQL
context without producer-lock reentry or argument disclosure. The test wrapper
uses a shared bounded submit/cancel queue and traces authorize_rest, with no
trading behavior changes. Diagnostic regression: 85 passed. This improvement
follows measurement and is not evidence of the old delay's cause or a passing
replacement pair. No production latency fix has been established; C stays open.

### Submit-path attribution — predeclared 2026-11-17

- [x] Before measurement, test stage instrumentation using the real throttler
  with its lock held. Waiting remains waiting; release consumes one original
  rate-limit entry; nested authorization/SQL spans retain their native submit
  parent, result identity and private-argument exclusion. One RED/GREEN case;
  86 nearby diagnostic tests pass. Only test assets change.
- [x] Execute exactly one candidate-only 120-second diagnostic probe with the
  same frozen image, 2 CPU /1GiB, 100,000 history rows, normalized book and
  security/defense fixture as the failed pair. Retain bounded MM/GC/incident
  capture, with native submit, throttle acquire and authorization spans. Observe
  AC/host CPU; no concurrent tests, CPU injection or replacement qualification.
- [x] Decompose retained slow submits using shared-kernel venue acceptance,
  throttle/authorization intervals and nested SQL. Distinguish queued time from
  synchronous work and unobserved intervals. If a specific production fault is
  demonstrated, reproduce it with a failing test before fixing it; otherwise
  keep C open and stop this bounded diagnostic with no production patch.

Result `20261117-111638-75a09d`: read p95/p99 132.87/237.87ms, no absolute
violations, 120 exact fills and matching positions/funding, no error/restart logs.
Of 22 native submit observations, 17 are >=100ms and all 17 have matched retained
native submit spans. Worst call: 306.714ms; venue acceptance at 263.552ms, return
43.163ms later. Authorization occupies 261.679ms; the entire pre-authorization
segment, including throttle, is bounded by 1.064ms. Its first traced owner read
starts 252.682ms into authorization, leaving 8.997ms to authorization return.
No captured GC or full-GC event overlaps the slow call; the loop keeps progressing.
Source queues both admission and final-send on submission_admission and yields
one loop turn inside that slot. Queue/turn scheduling is the supported explanation
for this new event; lock waiting and ready-task scheduling are not individually
measured. Retained SQL is incomplete because bounded child selection evicts nine
children from the root, so its 2.789ms sum is not total DB time. No production
fault is proven and no production path changes. Keep the earlier paired failure
and C open; this candidate-only diagnostic is not a passing replacement pair.

### Bounded final-send priority experiment — predeclared 2026-11-17

The operator authorizes testing a scheduling optimization. This is an experiment,
not evidence that the existing shared FIFO violates trading authority.

- [x] RED: twelve orders offered together, with equal admission/final proof work.
  Measure enqueue-to-venue acceptance as well as native-place time. Require
  earlier mean/first acceptance and non-regressing last acceptance, rather than
  merely moving a wait outside the native timer.
- [x] Keep one shared slot and FIFO within each class. Permit at most two final
  sends before a waiting admission. Preserve a loop turn per gate, fresh checks,
  cancellation/guard replacement, and release before network I/O. Test canceled
  queued and already-granted waiters, exceptions and continuous send backlog.
- [x] Compare fresh, equal real-PG twelve-order native bursts using FIFO and the
  candidate, three independent fixture pairs with equal SQL work/delay. Preserve
  end-to-end first/mean/last acceptance, query counts and callback progress.
- [x] Whole-path component evidence improves and authority regressions pass.
  Retain the optimization as an unqualified experiment; affected native/PG
  regression and a distinct frozen artifact are complete. Resource qualification
  below still fails; this does not approve release. Keep every old failure.

The logical burst is RED with FIFO first acceptance at work-unit 13 on both
sides; the bounded-priority candidate improves first/mean acceptance by the
asserted bound with no later last acceptance and identical proof-work count.
Three fresh native/PG pairs retain 396 SQL statements and twelve physical
send/ACK records per side. Mean enqueue-to-accept improves by approximately
26–33%; first acceptance is earlier and last acceptance is not later in all
three observed pairs. These are controlled component timings with a 2ms SQL
delay, not Cloud Run or exchange latency qualification.

Focused native checks pass (35 finally, including canceled-all handoff and
no-yield-after-final-proof races). The full Cloud Run native regression passes
4,051 tests +16 subtests before those two test-only cases are added. Focused PG is 44 passed; ownership/
new priority safety initially has 75 passed and one new-fixture failure because
legacy Start correctly rejects armed spot_outflow. The version-race fixture is
corrected to simulate an independent MM version writer; all eight new PG cases
then pass, including Stop/version after the first prioritized send. Full PG
regression passes all 997 tests across 116 modules in seven fresh process groups.
The separate candidate image matches all 1,134 runtime files. Scoped packaged
lifecycle and isolation pass; resource qualification below fails.

### Priority-artifact resource qualification — predeclared 2026-11-17

- [x] Finish packaged shutdown proof, managed credential lifecycle and
  upgrade/rollback using the new immutable candidate and the previous encoding
  artifact. This is scoped lifecycle coverage, not the complete release suite.
- [x] Run at most three sequential baseline/candidate pairs, each 120 seconds,
  2 CPU /1GiB, 100,000 history rows, healthy two-level external book with
  100,000 BASE depth, security/enhancements/volatility defense enabled. Use the
  original baseline manifest `102dbda3…`, not the previous Overview candidate.
  Candidate manifest is `2850a961…`. Preserve 500/1,000ms read p95/p99 budgets
  and p99 regression margin max(10% baseline, 10ms) for tick, tick gap, native
  submit and cancel. No replacement pairs; stop at the first failed pair.
- [ ] If all three pairs pass, run the canonical warmup (300s), mixed (900s),
  burst (90s), Random soak (3,600s) and six faults at 2 CPU /2GiB with keep-going.
  Retain independent ledger checks, bounded incident/GC capture and all failures.
  MM activity remains probe-only. No simultaneous builds/tests or CPU injection.
- [x] Exercise real-SQL Overview timeout isolation on this new image at 1GiB,
  using the retained candidate resource fixture, with two injected server waits.
  Observe durable fill progress, owner/process continuity and stale fee state.
- [x] Record AC and host process CPU throughout measurements; shared-host
  contention is a limitation even with operator idle confirmation. Retain the
  old warmup/cancel/submit failures separately; new results never replace them.

Raw scope: `build/overview-admission-scheduling-20261117/qualification`.
No Cloud Run change, production secret, risk policy or automatic commit/deploy.

Results: packaged shutdown proof `20261117-122221-c94b58`, managed lifecycle
`20261117-122334-1dc5bc` and upgrade/rollback `20261117-122929-ca3303` all pass.
The first resource pair (`20261117-123136-0c8d02` /
`20261117-123534-68d4b5`) passes absolute read budgets with 120 exact fills per
side, matching positions and peak memory below 1GiB. However, tick-gap p99
1,013.906 ->1,129.261ms and cancel p99 40.802 ->111.973ms exceed unchanged
margins. Submit p99 improves 272.785 ->204.643ms; tick p99 is unchanged
5.011 ->5.022ms. Native submit/cancel samples are 21/25 per side; these are
empirical cohort observations, not identical order-cohort comparisons. Keep
this pair failed. Remaining pairs and canonical load/soak are not started.

Exact-window follow-up retains the worst 1,278.911ms tick gap overlapping
`SpotOutflowProjection.advance`: 271.689ms wall /269.928ms thread CPU inside
`service.observe_once`. This is the largest gap, not the second-largest p99
sample of 1,129.261ms. No captured >=10ms GC overlaps either slow cancel.
The two cancels take 111.973/103.200ms; venue decomposition is respectively
76.914/69.846ms before request, 0.014625/0.008334ms simulator processing,
and 35.045/33.345ms after terminal until native return. Incident mode captures
the markers and sampled SQL context, but native cancel spans require full trace;
the exact cause of those waits is unproven. Do not relax the gate or risk guard.

Real-SQL Overview isolation `20261117-124133-5824aa` passes at 1GiB with two
SQLSTATE 57014 timeouts, a durable fill witnessed during the wait, 120 exact
venue/recorder fills, unchanged engine PID/owner/session, no HALT and clean Stop.
Fees keep a last usable value marked stale then recover. This is server-wait
isolation, not CPU-heavy SQL or Cloud Run qualification. C remains open.

Open follow-ups are distinct: bound bulk outflow expiry without weakening its
financial proof; capture exact native cancel/authorization timing if pursuing
that tail; then qualify a final image with the original margins. No additional
production changes are justified solely by an overlapping sample.

### Cooperative outflow clock catch-up — predeclared 2026-11-17

- [x] Reproduce bulk-expiry event-loop starvation through the actual service
  observer with 4,096 and 100,000 fills. Require callback progress before all
  expiry work completes, rather than a machine-specific millisecond assertion.
- [x] Bound each owner-loop advance to 512 heap/fill work items; preserve
  timestamp/key order and Decimal operation order. Keep the unlimited reference
  API for offline comparisons. Retain queued committed fills with detached
  payloads and replay in order after due work; do not expose partial risk as
  permission. BUY waits explicitly while catch-up is incomplete; SELL/cancel
  and Stop callbacks retain progress. Do not move mutable risk state to threads.
- [x] Test exact settled totals/fees/reservations, future timestamps, pending-fill
  replay/mutation/overflow, cancellation, and control/owner changes between
  batches. Resume safely without resetting checkpoints or risk limits.
- [x] Run affected native/PG regressions and freeze a distinct artifact. Execute
  one 120-second full-trace candidate diagnostic for native cancel attribution;
  it is not a replacement resource pair. Then run a new fixed resource scope
  with original baseline/margins and preserve failures; canonical only if all
  three pairs pass. No real Cloud Run or automatic commit/deploy.

The cooperative-expiry source fingerprint is
`24645f512022fccf084ad53af45c6cf1e913648630d4fad7a19ac3b28e7dac19`
(1,134 runtime files). Its predeclared artifact scope is distinct from the failed
priority pair: scoped shutdown/managed lifecycle/upgrade; exactly one 120-second
full-trace candidate diagnostic at 2 CPU /1GiB for cancel attribution; then at
most three original-baseline/candidate pairs, 120 seconds each, with 100,000
history rows, two-level 100,000 BASE book, security/enhancements/defense and
bounded MM/GC/incident capture. Keep read p95/p99 <=500/1,000ms and relative
p99 margin max(10% baseline, 10ms) for tick, tick gap, native submit and cancel.
Stop at the first failed pair. Only three passing pairs permit the canonical
300s/900s/90s/3,600s warmup/mixed/burst/Random soak and six faults at 2 CPU /2GiB.
Retain fresh Overview real-SQL isolation at 1GiB separately. No CPU injection,
concurrent tests/builds during measured windows, replacement pairs or relaxed
thresholds. AC/host samples remain mandatory and shared-host caveats remain.
Raw evidence: `build/overview-outflow-expiry-20261117`.

The cooperative artifact's first pair fails only cancel p99 (38.846 ->66.429ms),
with tick/gap/submit passing. Keep the failed pair and stop remaining pairs/soak.
Predeclare one additional candidate-only 120-second full-trace diagnostic at the
same 2 CPU /1GiB/history/book/security workload, with capture threshold lowered
from 100ms to 25ms in a detached test asset. Preserve the original and injected
asset hashes and actual threshold in raw evidence. No business runtime overlay,
new resource pass, guard change or replacement pair is implied. This scope is
needed because the 66ms failure falls below the prior trace threshold.


Cooperative result: all 4,074 native tests +16 subtests, 95 focused checks, all
250 affected PostgreSQL tests and the three scoped packaged scenarios pass.
The first resource pair passes absolute budgets and tick/tick-gap/submit
comparisons, with 120 exact fills and matching positions per side. Cancel p99
fails 38.846 ->66.429ms (+27.583ms against 10ms); keep the pair failed and do not
start remaining pairs/canonical. Full-trace 100ms captures no native cancel
root/child. The separate 25ms diagnostic keeps eight roots; the largest is
42.766ms with 0.071ms throttle, 1.307ms authorization and 40.965ms after
authorization until native return, zero dropped children. It does not reconstruct
the failed 66.429ms sample or split transport from loop scheduling. No speculative
cancel/authority patch is made. C and connected Demo remain open. SQL isolation
`20261117-142129-f55b59` on this exact image passes at 1GiB: two real timeouts,
a durable fill during the fault, 120 exact fills, matching positions, unchanged
engine PID/owner/session, no HALT and clean Stop. C remains open for cancel p99;
no commit, deployment, risk limit or real environment is changed.

### Same-pair cancel timeline — predeclared 2026-11-17

- [x] RED/GREEN the actual aiohttp pipeline: terminal callback before REST ACK,
  identical request/body/headers, return/exception/cancellation preservation,
  concurrent context separation and sink failure. Keep HTTP header-preparation
  hooks separate from actual transport-enqueue intervals.
- [x] Record every native cancel attempt and transport/body/callback boundary in
  a dedicated bounded 8MiB/256-queue fixture stream, independent of the 100ms
  incident threshold. Independently retain received/terminal/response-ready/EOF
  at the venue with a bounded request sequence. No correlation header is sent.
- [x] Refuse ambiguous or missing joins. Callback completion is distinct from
  native REST return; response headers may precede server EOF. Preserve overlap
  bounds and signed observations instead of inventing zero/negative network time.
- [x] Execute exactly one sequential original-baseline/cooperative-candidate
  diagnostic pair: 120s each, 2 CPU /1GiB, 100k history, the original two-level
  100k BASE/security/defense workload, MM/GC/incident capture and the same new
  timeline assets on both sides. Record all asset hashes and AC/host observations.
  Keep the previous failed 38.846 ->66.429ms cancel pair failed. This one paired
  diagnostic cannot replace it or qualify a release, regardless of its result.
- [x] Decompose matched slow calls in that same measured pair, including venue
  and callback observation. Only a demonstrated production fault warrants a new
  RED reproduction/fix; preserve owner/Stop/version checks and all thresholds.

Business runtime remains the frozen cooperative artifact, fingerprint
`24645f512022fccf084ad53af45c6cf1e913648630d4fad7a19ac3b28e7dac19`.
Only offline fixture/analysis tests change in this scope. Raw evidence:
`build/overview-cancel-timeline-20261117`. No automatic commit/deploy/Cloud change.

Result: diagnostic baseline `20261117-151138-68179e` passes absolute budgets;
candidate `20261117-151535-02881d` fails read p95/p99 and all four relative
comparisons. Cancel p99 is 31.310 ->339.461ms. Preserve both this failure and the
earlier 38.846 ->66.429ms failure; no replacement pairs or canonical soak run.
The 192 nearby diagnostic/web-assistant tests pass. All 1,134 frozen business
runtime hashes remain unchanged.

The largest candidate cancel matches one unique venue request: terminal observed
in 0.063ms, response ready another 0.590ms later, then 326.738ms until client
headers are recorded. Connection return ->caller resumption is only 0.038ms.
The headers marker follows client coroutine resumption; this interval still
combines transport/I/O dispatch/scheduling and cannot prove pure network time.
A recorder checkpoint is sampled during the interval, but its completed trace
is not retained in the 100ms-threshold capture; no exact blocking duration is established.
The overlapping async observe span has 11 dropped children and inclusive CPU,
so it cannot attribute the entire delay to one function. No fresh throttling
counter increase occurs between the two bracketing CPU samples. Host samples
show concurrent Go/clang/VS Code CPU activity despite prior idle confirmation.
This supports a shared-host caveat, not a unique causal diagnosis.

No demonstrated production fault warrants a speculative patch in this scope.
The diagnostic capture/decomposition is complete; resource gate C, canonical
soak and connected Demo qualification remain open. A controlled resource pair
still needs a quiet host or independent worker before qualification continues.
