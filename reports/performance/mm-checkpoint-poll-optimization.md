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
