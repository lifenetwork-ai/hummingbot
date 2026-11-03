# Position checkpoint and background-poll optimization

Date: 2026-11-03 (Asia/Saigon).
[Plan](../../docs/plans/MM_CHECKPOINT_POLL_OPTIMIZATION_TDD_PLAN.md).

## Position checkpoint

Previously each hold performed its own lookup, flush/fence and commit. The
orchestrator now prepares every snapshot before writing, then the existing
recorder looks up all natural keys once and commits the complete batch once.
The single-position method delegates to the same path. Repeated input keys keep
the last snapshot; existing primary IDs remain unchanged. No schema, Decimal,
valuation, terminal-proof or owner-fence policy changes.

Four new PostgreSQL cases fail Red before implementation (missing batch method);
the native failed-write/retry test also fails Red because the old orchestrator
never submits a batch. Green: 71 native recorder/orchestrator/terminal-proof
tests and 17 PostgreSQL batch/initial-exposure/drawdown tests pass. The measured
query/commit budget is one position lookup and one recorder-session commit for
the mixed insert/update batch. Exact 18-decimal amounts/fees, duplicate input,
rollback, stale owner and empty/no-checkout paths pass. A rejected commit leaves
holds resident and does not proceed to executor persistence.

This reduces N per-position lookup/commit cycles to one; the database still
performs the necessary row writes. Recorder IO remains synchronous on the engine
loop. Moving that IO to a worker requires a separately reviewed fence/lifecycle
change: the current hook calls the dedicated owner connection, which must not be
shared across threads. These tests do not establish a millisecond speedup, a
historical probe root cause or new image/load/Demo acceptance.

## Idle reconciliation polling

The previous one-second idle poll first acquired FOR UPDATE on bot_state, then
read the receipt queue. It now issues a single non-locking eligible-ID probe.
Actual work still locks bot_state first, verifies owner/generation and re-reads
the eligible receipt in that transaction. An arriving request missed by the probe
waits for the normal next poll. There is no cache or authority shortcut.

Three new cases fail Red on the old implementation; the stale-owner/reclaim
case already passes and remains a compatibility check. Green: 54 PostgreSQL
reconciliation/ownership tests and 42 native observer/publication/reconciliation
tests pass. An empty queue requires one SQL query and no control-row lock; it
also completes while a separate transaction holds that row, under a 150ms lock
deadline. Candidate-completed and owner-changed races return no claim; current
owners can still reclaim applying work. This removes idle lock contention rather
than promising a particular HTTP/probe latency reduction.

## Funding history discovery

The previous GROUP BY/MIN query revisited every perpetual order each poll.
The replacement performs index-boundary seeks: select the first symbol/earliest
timestamp, then select the first row strictly past that symbol, until exhausted.
It reuses idx_orders_market_symbol_creation_timestamp, in one fresh SQL
statement. There is no pair allowlist, time cutoff, cache, migration or new index.
Historical pairs outside YAML and newly imported older timestamps remain visible.

[EXPLAIN evidence](mm-checkpoint-poll-evidence.json) records PostgreSQL14.15,
native arm64, 2 CPUs/512MiB, the full migrated schema and one million generated
orders (800,000 perpetual, four perpetual pairs). After VACUUM ANALYZE, five
sequential EXPLAIN ANALYZE/BUFFERS samples per query give:

| Query | Execution median | Range | Last-plan leaf row visits | Shared hit/read blocks |
| --- | --- | --- | --- | --- |
| Previous aggregate | 170.867ms | 132.307–373.513ms | 800,001 | 6,229 / 16,010 |
| Index-boundary discovery | 0.115ms | 0.105–0.166ms | 9 | 19 / 0 |

Both queries return exactly the same four symbol/minimum-timestamp rows. The
measured candidate matches the production SQL after whitespace normalization.
These are server execution measurements, with the candidate measured after the
legacy samples, not randomized cold-cache or end-to-end engine/API comparisons.
Only fixture loading/VACUUM use extended test IO budgets; no runtime limit changes.

The row-visit regression fails Red at 4,000 visits on a 5,000-order fixture;
Green requires fewer than100 (not a timing-sensitive threshold). Empty/spot-only,
unconfigured-pair and late-import tests also pass. Relevant checks: 14 PostgreSQL
funding cases and 24 native funding-observer/sync cases. Each discovery still
costs one SQL round trip; its work now tracks pair count and index access rather
than every historical order. Full raw setup/plans remain under
build/checkpoint-poll-optimization-20261103/; the disposable database is removed.

## Implementation commits and acceptance boundary

- 9814756d1: atomic batched terminal position checkpoints.
- a37240127: non-locking empty reconciliation polls, with locked claim revalidation.
- bdf7ce0a5: funding discovery through the existing composite order index.

The broad native run initially finds two remaining mocks asserting the old
single-position call. They are updated to assert one batch and the same exact
quantities/fees/disabled-market exposure; no runtime behavior is changed for
these failures. The original failed log is retained under build/.

No Demo credentials, trading intent, database contents, HALT or deployed service
is changed. The bot image/containers from the prior acceptance are absent from
the observed local Docker inventory; only owned disposable PostgreSQL test
resources are created. The prior 660bd38b hour/load/Demo evidence remains scoped
to that image. No new image, packaged E2E, soak, connected Demo or deployment is
claimed by this source-level optimization.

Combined native verification: make test-cloudrun-regression passes all four
groups (178 + 11 + 1,534 + 634 = 2,357 tests, plus 11 subtests); Swagger contract
check also passes. Native/PG source checks are distinct from packaged/load gates.

Full PostgreSQL verification also passes: 307 tests in384.32s, on a fresh
disposable database. Its container is removed by the runner. Final command logs:
build/checkpoint-poll-optimization-20261103/native-regression.log and
build/checkpoint-poll-optimization-20261103/postgres-regression.log.

Commands (using the repository's native hummingbot Python environment):

```sh
make test-cloudrun-regression PYTHON=/path/to/hummingbot/bin/python
python scripts/test_postgres.py
python -m hummingbot.cli.swagger --check
```

Remaining boundary: recorder IO is still synchronous, and no new measured API
p99/trading-throughput, indefinite-memory, uninterrupted-quote or historical
probe-root-cause conclusion follows from these changes.

## Pre-load follow-up: OKX spot net direction is mutable

During the requested load/Demo rerun, the original local Demo volume is found
deleted. Its latest backup has six fills/1,257 orders and a spot BUY hold of
0.00025419BTC; the following independently verified maker SELL is0.00025828BTC.
That crosses the managed spot net to SELL0.00000409BTC. An isolated reproduction
shows the inherited four-column lookup can insert that SELL while retaining the
old BUY, causing a restarted orchestrator to load extra exposure. This behavior
predates batching. The deleted original database cannot be inspected for the
actual post-fill spot rows; prior venue/fill/perpetual proofs are not presented
as full spot-net-reversal acceptance.

Three new PostgreSQL tests fail Red on the batched pre-fix writer. OKX spot
identity now uses controller/connector/pair, updates direction in the existing
row and preserves its ID. Perpetual hedge legs retain their separate side keys.
Opposite input snapshots or multiple existing spot aggregates are rejected
before writing any batch member, requiring reviewed reconciliation instead of
silently discarding history/exposure. No automatic data repair, deletion or HALT
reset is added. Reverse flips, zero net, repeated writes and restored signed
exposure pass; full source checks repeat successfully: native2,357 +11 subtests,
PostgreSQL312. Old image6d9f9f01 passes three E2E groups but is intentionally
superseded before R1 starts, so it cannot qualify the corrective source's load.
