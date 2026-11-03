# Controller and persistence optimization

Started: 2026-11-03. Completed: 2026-11-04 (Asia/Saigon).
[Plan](../../docs/plans/MM_CONTROLLER_PERSISTENCE_OPTIMIZATION_TDD_PLAN.md).
Raw evidence: `build/mm-controller-persistence-20261103/`.

## P1 — One exposure read per synchronous decision

A healthy six-level controller previously calculated exposure three times,
including three spot balance reads even with no new actions. The new path reads
once: a report-ready cycle delegates its fresh exposure read to the synchronous
decision; a waiting-for-report cycle still checks exposure independently for
protective cancellation. The local tuple is passed only to the private proposal
builder, which still reuses upstream create/stop implementations and preserves
create-then-stop ordering. It is never retained on the controller or across awaits.
Public readiness checks and direct proposals continue reading fresh account data;
the post-throttle physical-send fences are unchanged.

Four Red cases establish the duplicate reads. Boundary tests separately expose an
inherited validation defect: a spot balance of -1 plus 30 reserved BUY units becomes
29 and incorrectly passes the final nonnegative aggregate check. The two native
cases fail both with and without a ready executor report. A real-PG post-throttle
case also reaches the mocked transport instead of blocking. Raw spot balance is
now validated before reservations are added; invalid observations cannot be
masked. This is a synthetic adverse-input reproduction, not an observed Demo loss.

P1 relevant native verification: **457 passed plus two subtests**. The real-PG
joint-readiness/recorder/shutdown module passes **11 cases**, including the new
negative-balance regression. This is a 3→1 work-count reduction, not a measured
threefold improvement in engine latency. Combined verification is recorded below.

## P2 — One scoped snapshot identity query

Each PostgreSQL snapshot previously selected admitted tracking IDs, then selected
pending IDs separately to retain identities after a lost acknowledgement. The new
query returns the union, scoped to both config and connector, with the pending
flag. Unadmitted queued siblings are still excluded, and missing pending IDs retain
their prior durable tracking payload. Empty snapshots and the file backend keep
their previous single pending lookup. No ownership/unknown-intent query, commit,
full JSON snapshot write or final physical-send check has been removed.

The real-PG Red run has **six query-count failures and three semantic passes**;
Green has **nine passes**. A subsequent parameter adds the nonempty-tracker peer
variant tests retention through both query branches; all ten new cases pass in
the full suite below. Rollback, lost acknowledgement, config/market isolation and
queued-but-unadmitted orders are covered.

Three fresh-fixture repetitions per size use 100,000 closed historical orders.
These are instrumented durable-admission cohorts, not physical order sends:

| Metric | 12 intents before → after | 64 intents before → after |
| --- | --- | --- |
| Snapshot filter SELECTs | 24 → 12 | 128 → 64 |
| Total SQL statements | 168 → 156 | 896 → 832 |
| Median cumulative snapshot-call time | 98.95 → 103.16 ms | 635.39 → 620.18 ms |
| Median cohort time | 322.57 → 381.30 ms | 2090.16 → 1967.68 ms |

All six final snapshot hashes and cumulative compact JSON byte counts match.
The measured changed queries use the existing primary-key and pending-order indexes;
EXPLAIN ANALYZE checks fewer than 1,000 visited rows despite the closed history.
The deterministic improvement is **50% fewer snapshot filter queries and 7.1%
fewer overall SQL statements**. Timings are mixed: the smaller cohort regresses
in these samples. Three fixed-order local repeats cannot establish a production
latency improvement. Full snapshots and global intent checks remain measured costs;
removing their safety proof would need a different, separately tested design.

## P3 — Retain fresh readiness calculations

Actual host readiness, reference/exposure math and fee composition were profiled
with cached account/book reads mocked, six working levels per controller, fee
floor enabled and a static 0.1% maker fee. Each controller does one reference
update, one exposure read, one compose and six fee reads per invocation. Five
100-call repetitions show median **0.38 ms spot / 0.89 ms both** per call.
The full samples and cProfile totals are in the evidence. Regression startup ran
concurrently, and mocks add their own overhead; these are local scope measurements.
Volatility candles, bootstrap and drawdown are outside this fixture.

This gives no reason to introduce another cache or weaken fresh post-throttle
checks. It does not explain or resolve historical seconds-long probe episodes.
No price/account/ownership TTL cache, Redis, new service, schema or index is added.

Re-run the focused measurement against an isolated PostgreSQL fixture with:

```sh
HBOT_PERFORMANCE_REPORT=build/tracking-snapshot-work.json \
  python scripts/test_postgres.py -k test_tracking_snapshot_work
```

The runner owns its disposable test database. The performance JSON is optional;
test assertions cover query count, scoped contents, rollback and query-plan work,
with no wall-clock threshold. Baseline evidence comes from the P1 source before
the recorder change; the retained baseline test run intentionally fails the six
new single-query assertions.

## Verification and qualification

[Portable evidence](mm-controller-persistence-evidence.json) retains the measured
counts, timings, plans and snapshot hashes. Canonical native regression passes
**2,442 tests plus 11 subtests** (178 + 11 + 1,538 + 715 cases across four pytest
invocations); Swagger contract validation and pre-commit checks pass.

The full PostgreSQL run has **342 passes and one failure**, an existing fill
performance test that still asserts the old exact 12-statement budget. Its observed
count is 11 in both indexed/unindexed variants. Updating that assertion to exactly
11 (no runtime change) and rerunning the complete performance module gives
**11 passes**, including exact ledger fill-count and quantity checks. This covers
**343 distinct PostgreSQL cases across the two runs**, with ten repeated passes;
it is not presented as a single clean 343-case run. The ten new tracking cases all
pass in the full run. Initial failure and scoped rerun are retained in evidence.

The final fenced-fill fixture with 100,000 historical orders records 11 statements
and 25 samples per variant. Its indexed p50/p95 are 24.36/30.87 ms; unindexed are
43.42/48.78 ms. This compares index availability on the new source, not the
recorder change against its baseline. No extra index is introduced.

These changes have **source-only** verification. Accepted image `51d4d879` is
unchanged and does not contain this implementation. No new image, load, Demo or
deployment acceptance is claimed, and no Demo ledger or HALT is modified.
