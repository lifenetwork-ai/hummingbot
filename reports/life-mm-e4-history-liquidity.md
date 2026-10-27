# E4: transition history and sampled liquidity

Verified 2026-10-27 (Asia/Ho_Chi_Minh), baseline `b1322fb39`.

Reused existing control transactions, owner fencing, migrations, status publication,
order tracker, keyset cursors, authentication and handler OpenAPI. Added one bounded
`mm_events` model/migration and a small in-process observation projection. No new
service, task, cache, trading engine or exchange request path.

TDD caught guard history disappearing after controllers stopped, stale-generation
snapshots looking fresh, market orders being counted as quotes, optional malformed
projections breaking publication, elapsed counters growing after stop, unnecessary
SAVEPOINT on quiet ticks, and filtered keyset scans lacking an appropriate index.
Each regression was reproduced before its fix. Savepoints now isolate actual
optional guard-event writes only; required control/event transactions still roll
back atomically. Two filter indexes remove the demonstrated scan issue.

Native regression passed 1538 tests and eleven subtests (158/11/979/390). Full real
PostgreSQL regression passed 182 cases; the final targeted history suite passed all
12 cases, including the added stopped-controller global guard regression. These
cover migration replay/schema parity, rollback/retry/ownership, configuration
preservation, repeated random draws, HTTP auth/bounds/UTC, pagination/filter binding,
constant two-query reads over 1000 events, index-backed event filtering and FE
polling without exchange calls. Eight liquidity unit cases cover partial/pending
orders, gaps, stale data, zero quotes, stopped sessions and bounded checkpoints.
Scoped hooks and generated Swagger are checked before commit.

See [API guide](../docs/fe-integration.md#session-history-and-liquidity). Coverage is deliberately
sampled; no fabricated pre-migration history, historical depth time series or
exact profile PnL. Final full-image matrix/load/soak remain E6 and are not claimed
by this phase. No external OKX account or deployment was used.
