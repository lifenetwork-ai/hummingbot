# Service availability audit remediation — TDD

Baseline audit: [five findings](life-mm-availability-audit.md). No cloud deployment or
live trading is part of this work. Retain the original audit/probe evidence.

- [x] AUD-001: Immediately fence shutdown new orders/account setup; preserve cancels.
- [ ] AUD-002: Isolate optional PostgreSQL statement/lock failures; preserve mandatory failures.
- [ ] AUD-003: Bound established DB I/O and prove fenced recovery under response loss.
- [ ] AUD-004: Bound API admission and prioritize control without increasing DB concurrency.
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
