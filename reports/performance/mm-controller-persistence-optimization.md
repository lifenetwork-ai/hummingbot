# Controller and persistence optimization

Date: 2026-11-03 (Asia/Saigon).
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
inherited validation defect: a spot balance of-1 plus30 reserved BUY units becomes
29 and incorrectly passes the final nonnegative aggregate check. The two native
cases fail both with and without a ready executor report. A real-PG post-throttle
case also reaches the mocked transport instead of blocking. Raw spot balance is
now validated before reservations are added; invalid observations cannot be
masked. This is a synthetic adverse-input reproduction, not an observed Demo loss.

P1 relevant native verification: **457 passed plus two subtests**. The real-PG
joint-readiness/recorder/shutdown module passes **11 cases**, including the new
negative-balance regression. This is a3→1 work-count reduction, not a measured
threefold improvement in engine latency. Full combined verification follows P2.

## Next measurement

P2 will measure PostgreSQL tracking snapshot query/cohort cost before selecting
an optimization. Global unknown-intent detection, commit-before-send, final fresh
checks and SQLite/file behavior remain required. P3 readiness math is considered
only with measurements; no risk-data TTL cache or new architecture is authorized
by a presumed speedup. No new image/load/Demo or deployment acceptance is claimed.
