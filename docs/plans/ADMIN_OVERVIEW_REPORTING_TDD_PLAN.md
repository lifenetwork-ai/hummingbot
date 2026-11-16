# Admin Overview reporting — TDD remediation

Updated 2026-11-16 local. Status: planned; implementation and qualification remain open.

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

- [ ] Incorporate the PR on an implementation branch without dropping current dev fixes.
- [ ] Freeze baseline source/fixture hashes and retain original RED evidence.
- [ ] Complete A, B and D with focused regression tests.
- [ ] Complete C against the final source and immutable image.
- [ ] Generate contracts, update FE documentation and record acceptance by scope.

## A. Correct asset units and decimal serialization

Targets: `hummingbot/cli/admin_overview_models.py`, `admin_overview.py`, existing
market capture, and their contract/projection/HTTP tests.

### RED

- [ ] Run the three retained counterexamples against the actual PR modules.
- [ ] Parameterize Spot/perpetual, BTC/LIFE and another valid configured base asset:
  `inventory_limits` remains available and each risk quantity carries its actual
  asset. Long, short and gross caps retain their distinct meanings.
- [ ] Reject or mark a mismatched asset/pair unavailable; changing the schema to
  unrestricted strings must not admit contradictory units.
- [ ] Verify wire serialization for small fees/rebates, tiny quantities, zero,
  large values and significant decimals through the real HTTP response. Require
  plain decimal strings and exact values; reject non-finite values.
- [ ] Check the generated schema and existing FE decimal arithmetic against those
  payloads. A serializer-only test is insufficient.

### GREEN and acceptance

- [ ] Derive inventory asset from the configured pair; represent quantity units
  explicitly, including in risk summaries. Keep USDT/bps/time units distinct.
- [ ] Apply a shared plain-decimal serializer to the Overview financial types;
  avoid float conversion or UI-specific rounding.
- [ ] Regenerate OpenAPI and TypeScript types; no handwritten generated edits.
- [ ] Add counterexamples to normal regression. Existing unknown/freshness and
  partial-metric isolation tests still pass; no exchange request is introduced.

## B. Eligible two-sided coverage over a rolling 30-minute window

Targets: `mm_liquidity.py`, `overview_capture.py`, Overview coverage models,
projection and existing quote diagnostics. Reuse resident observations; do not
add exchange polling or call an admission helper with side effects.

### Measurement contract before implementation

Preserve `rolling_30m_fraction = two_sided_seconds / elapsed_seconds` as the
all-time view. Add an independent eligibility classification; the existing
controller-readiness `eligible_seconds` counter is not the approved SLO.

- [ ] An eligible interval requires an admitted running session, fresh evidence
  that both sides are funded, and no explicit current guard/inventory/bootstrap
  restriction. Normal replacement/pending ACK does not excuse a missing quote.
- [ ] Classify intervals as eligible, explicitly restricted with a reason, or
  eligibility-unknown. Missing/stale/inconsistent evidence is unknown.
- [ ] Add `eligible_seconds`, `two_sided_eligible_seconds`, `restricted_seconds`,
  a bounded restriction-reason breakdown and `eligibility_unknown_seconds`, with
  matching per-minute fields. These eligibility categories partition elapsed
  time; retain existing observation `unknown_seconds` with its original meaning.
- [ ] Publish the conditional eligible fraction for diagnostics, plus an explicit
  conservative fraction `two_sided_eligible_seconds /
  (eligible_seconds + eligibility_unknown_seconds)`. Document both denominators;
  unknown never becomes successful quoting. A zero eligible denominator is
  unqualified, not 100%. Do not overwrite the old all-time fraction.
- [ ] Keep `full_window`, original observation timestamps, market, session and
  generation scope. Publish measurement readiness/reason separately from the
  numeric result. Missing target configuration remains null; never infer a target
  from the data. The approved 0.99 target is a qualification input, not enforcement.

### RED matrix

- [ ] A full window with 900s restricted and 900s eligible/two-sided yields 50%
  all-time and 100% conditional/conservative eligible coverage. Restriction stays visible.
- [ ] A window with 900s eligible/two-sided and 900s eligibility-unknown yields
  100% conditional and 50% conservative coverage; it cannot pass the 99% target.
- [ ] Ordinary refresh, pending create/cancel and delayed ACK remain eligible
  when all eligibility evidence is fresh; missing quotes reduce the numerator.
- [ ] Test one-sided funding, long/short/gross caps, quote guard, bootstrap,
  missing guard evidence and stale private/public data independently.
- [ ] Test exact window/minute boundaries, partial windows, duplicate timestamps,
  clock reversal, monotonic/wall divergence, owner takeover, restart, generation
  change, Random step, Stop/end clipping and legacy checkpoints without history.
- [ ] Test high-frequency alternating states over a full 1,800 seconds. A bounded
  history must not silently lose coverage or turn dropped intervals into success.
- [ ] Assert partition/sum invariants and `two_sided_eligible <= eligible`; verify
  API/FE distinguish total-time coverage, conditional coverage and conservative SLO.

### GREEN and acceptance

- [ ] Store bounded interval/bucket history containing the classification and
  evidence needed for the approved definition; do not reconstruct it from
  cumulative counters or classify a whole interval from only its final sample.
- [ ] Clip transitions conservatively and preserve unknown gaps across changes.
  Bound memory/checkpoint size and retain private history only server-side.
- [ ] Use an independent reference timeline in tests for randomized transitions,
  rather than recomputing expected values with the implementation helper.
- [ ] Update OpenAPI/types and `docs/fe-integration.md`: exact formulas, reason
  codes, null behavior, sampled measurement limitations and SLO readiness.

## D. Session fee reporting beyond 10,000 fills

Targets: `hummingbot/model/overview_fees.py`, `overview_fee_cache.py`, admin DB
scheduling and new disposable-PostgreSQL projection tests. This stage may require
an additive migration; select its number from the implementation branch.

### Design to test

- [ ] Keep the current proven bot/session/account/environment attribution and
  conversion-at-fill rules. Source account binding comes from the submission
  context, not the connector's current key. Handle generations of one session
  without leaking another session or rotated account into its total.
- [ ] Build a reporting-only durable contribution projection, unique by the
  source fill identity plus projection/scope version. Each accepted contribution
  updates its aggregate/counts atomically; replay must have no additional effect.
- [ ] Process bounded batches on the existing heavy DB lane with single-flight,
  backpressure, statement/lock deadlines and a yield between batches. No extra
  process/pool and no new dependency in order admission, fill commit or send.
- [ ] Discover unprocessed source identities with indexed, bounded catch-up and
  reconciliation. A trade timestamp or sequence high-water mark alone is unsafe:
  delayed fills and transactions committing out of sequence must still be found.
- [ ] Aggregate exact Decimal contributions and conversion/attribution failures.
  Separate processing catch-up from financial history completeness. Finishing a
  backfill cannot turn unprovable legacy history into `history_complete=true`.
- [ ] Serve the last completed aggregate while catching up, with `processing_state`,
  `processed_fill_count`, processing observation time and `caught_up_at` (nullable).
  Define `through_at` precisely; maximum trade timestamp is not proof of coverage.
  Use `catching_up`/`unavailable` plus reason when no usable aggregate exists.
- [ ] Document startup, no-poll progress, stopped-session reads, retention and
  projection-version rebuild behavior. Published totals must remain scoped and
  readable across restart; no full-session scan on every status request.

### RED matrix on real disposable PostgreSQL

- [ ] 9,999 / 10,000 / 10,001 / 100,000 attributed fills in one until-stopped
  session produce the same exact total as an independent full-history oracle;
  there is no permanent row-limit unavailable state.
- [ ] New fills continue during catch-up; a late fill has an older trade timestamp.
  Include equal timestamps, sequence allocation before delayed commit, and a
  bounded set of fills whose attribution becomes provable after first discovery.
- [ ] Duplicate delivery, concurrent projection workers and repeated batch replay
  contribute each source fill once. Test different identities sharing timestamps.
- [ ] Kill a worker after reading sources, after inserting contributions, after
  aggregate update/before COMMIT, and after server COMMIT/before acknowledgment.
  Resume/retry converges with no missing or doubled fees using actual transactions.
- [ ] Inject lock timeout, SQL timeout, DB disconnect and process restart; keep
  the last usable result labelled correctly and allow subsequent catch-up.
- [ ] Isolate bot/session/account/Demo/Live scope, account rotation, zero fees,
  rebates, base-token fees and unknown third-token conversion. Never turn an
  unprovable amount into zero or include it in a supposedly complete total.
- [ ] Test legacy/empty history, additive migration from the current schema,
  repeated startup and previous-image compatibility. Restore/rollback behavior
  must be explicit if a previous binary cannot read the new projection.
- [ ] Exercise `ENABLE_AUTO_MIGRATION=true` with an active owner and overlapping
  revisions. New reporting tables/indexes must not invalidate financial schema
  checks, hold an unbounded runtime lock or require backfill inside startup.
  Run destructive fault/migration tests only against disposable databases.
- [ ] Check query plans/rows examined at 100k and 1m retained source fills, with
  multiple scopes. Bound per-batch and warm-read work, including anti-join discovery;
  a LIMIT on returned rows is not evidence of bounded database work.

### GREEN and acceptance

- [ ] Keep contributions, progress and aggregate in one transactional protocol;
  idempotency survives unknown COMMIT outcomes. Existing financial rows, journal
  applied flags, positions and risk checkpoints are not repurposed or rewritten.
- [ ] Status reads remain bounded as session history grows; background catch-up
  continues without UI polling and yields capacity to other heavy reports.
- [ ] Publish a rebuilt projection version atomically after verification; retain
  the last usable version during catch-up. Recheck cache freshness on each read,
  including stopped sessions and restart, without hiding its partial-history label.
- [ ] Document partial totals/reasons and update generated contracts. Migration
  and rollback checks pass before using the new image on connected Demo.

## C. Reporting isolation and resource qualification

Reuse `scripts/test_mm_load.py`, `scripts/test_mm_reporting_isolation.py`,
`scripts/mm_load_budgets.py` and the offline exchange/service/client fixtures.
Extend the harness for Overview rather than replace the existing workloads.

### RED / fault coverage

- [ ] Hold the actual Overview fee query/lock while concurrently polling status,
  issuing Stop and delivering exchange events/fills. Test cold-cache, concurrent
  readers, cancellation, timeout/recovery and shutdown with a physical worker busy.
- [ ] Assert `/status` uses bounded cached/pending reporting data; it does not wait
  for fee backfill to complete. Measure cold-cache behavior, including the PR's
  existing optional 200ms wait, rather than hiding it outside request timing.
- [ ] Fill the 30-minute coverage/recovery history to its supported bound; test
  cold construction, serialization, checkpoint publication and restored history.
- [ ] Fill reporting queues and slow DB work; prove operational/control capacity
  remains reserved. A cancelled client cannot falsely release a busy worker slot.
- [ ] Inject collector/projection failures without manufacturing trading faults:
  metrics become unavailable, while readiness/ownership/send gates retain their
  existing behavior. Do not suppress genuine HALT/ownership failures to pass.

### Paired load and acceptance

- [ ] Compare a frozen baseline and candidate using identical fixtures, CPU,
  memory, history size, rates, guards, durations and warmup; repeat the paired
  component window three times. Report absolute results and deltas, including
  queue wait, worker time, event-loop delay, tick progress, ACK/cancel timing,
  snapshot bytes, SQL work, throughput and retained memory.
- [ ] Keep existing gates unchanged: read p95 <=500ms/p99 <=1,000ms, command accept
  p95 <=500ms/p99 <=1,000ms, Start/Switch <=15s, Stop <=5s, and existing load rates,
  backlog, error, financial integrity and retention limits. Healthy reporting
  faults must not cause a restart/HALT or loss of trading progress.
- [ ] Run a separate production-shaped 2 CPU / 1,024MiB cgroup qualification and
  retain its hard memory limit; the historical 1.5GiB RSS allowance cannot stand
  in for success under a 1GiB deployment. Keep the canonical load results separate.
- [ ] Before collecting results, freeze additional resource-regression budgets:
  candidate tick/ACK/cancel p99 must not regress beyond a declared noise margin;
  start with max(10% of baseline, 10ms), measured on identical paired windows.
  This is a proposed local regression gate, not a new exchange latency promise.
  Predeclare any adjustment from baseline variability, never after candidate failure.
- [ ] Complete unchanged warmup/mixed/burst, Random soak 3,600s and existing fault
  windows on the final immutable image; include Overview refresh/catch-up. Retain
  every slow probe and failure with its interval and evidence. Do not classify a
  reporting probe as proof that MM stopped, or use fills alone as proof of quotes.

## Verification and closeout checklist

- [ ] A: focused model/projection/HTTP tests and existing Overview regressions.
- [ ] B: timeline/reference tests, continuity/freshness tests, API contract and
  FE decimal/coverage integration checks using generated types.
- [ ] D: disposable PostgreSQL integration, migration, retry/crash/unknown-COMMIT
  and large-history query-plan tests; never point destructive fixtures at Demo DB.
- [ ] C: native readiness/report/owner regression, packaged offline lifecycle,
  load/isolation, full soak and unchanged financial-ledger checks.
- [ ] Run `make swagger` and the existing schema generation/check pipeline, then
  dashboard lint/typecheck/unit/build checks. Reuse existing browser tests; any
  additional UI acceptance is a separately scoped implementation task.
- [ ] Record tested commit, image digest, source/fixture hashes, resource settings,
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
