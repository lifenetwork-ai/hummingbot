# Service availability audit remediation — TDD

Baseline audit: [five findings](life-mm-availability-audit.md). No cloud deployment or
live trading is part of this work. Retain the original audit/probe evidence.

- [x] AUD-001: Immediately fence shutdown new orders/account setup; preserve cancels.
- [x] AUD-002: Isolate optional PostgreSQL statement/lock failures; preserve mandatory failures.
- [x] AUD-003: Bound established DB I/O and prove fenced recovery under response loss.
- [x] AUD-004: Bound API admission and prioritize control without increasing DB concurrency.
- [ ] AUD-005: Supervise the required executor-action consumer.
- [ ] Re-run regression, real PostgreSQL, Swagger and a newly verified immutable image.
- [ ] Re-run the unchanged healthy/fault load gates on the final source/image.

Each fix records Red/Green before its local commit. Prior image/load results remain
historical, not acceptance for modified runtime.

## AUD-001

Red: seven new process-shutdown cases failed before implementation. Green: 39
focused shutdown/service/availability/lifecycle tests pass. The owner now has a
process-lifetime shutdown latch and a stop-event predicate, checked before and
after its synchronous ownership query for new risk. Cleanup closes order/setup
permission before awaiting optional tasks; cancel/read fencing remains active.
Raw logs: `build/availability-fixes/aud001-red.log` and `aud001-green.log`.

## AUD-002

Red: three new real-PostgreSQL cases failed (daily row lock, canceled optional
statement and guard-history table lock); five fatal-error classification cases
already passed. Green: 14 focused optional/critical publication/event tests pass.
Only SQLSTATE 55P03/57014 on a non-invalidated connection may degrade an optional
write after its savepoint rollback. Lost connections, mandatory writes/commits,
unknown operational errors and recovery rules remain fatal. Daily observation
backoff is reused. Raw logs: `aud002-red.log` / `aud002-green.log`.

## AUD-003

Red: five subprocess/proxy response-loss cases failed before the driver change.
Green: 27 real-PostgreSQL cases pass, including query, commit, rollback, streamed
fetch and owner loss, optional statement failures and migration tests; six native
deadline tests pass. The owner worker fixture explicitly registers existing ORM
models before acquisition (its initial setup error was not a runtime defect).
The existing Psycopg wait-callback mechanism bounds managed runtime operations to
3s and discards the physical session on failure. Owner loss remains latched and
its replacement requires recovery. Bootstrap retains its longer total budget.
This bounds synchronous stalls; it does not make DB work asynchronous. No SQL,
DSN or credentials enter the timeout event. Raw logs: `aud003-red.log`,
`aud003-green.log`, `aud003-unit-red.log` and `aud003-unit-green.log`.

## AUD-004

Red: two new native tests and the seven-slow-reads/Stop HTTP probe fail before
the change. Green: nine native executor/cache tests and four real-PostgreSQL
control/capacity/workflow tests pass. A small dispatcher reuses the standard
two-worker thread executor, admits at most 16 outstanding jobs (12 reports),
and puts commands/individual command receipts ahead of pending reports. Already
running jobs retain their capacity after client cancellation. Close cancels the
pending queue and joins running workers; exceptions release admission normally.
The HTTP probe accepts Stop and reads its receipt each within 2.7s despite seven
1.8s reads, retaining an available owner connection. Pool size stays five.
Raw logs: `aud004-unit-red.log`, `aud004-pg-red.log`, `aud004-unit-green.log`
and `aud004-pg-green.log`.
