# Reporting and startup optimization

Date: 2026-11-02 (Asia/Saigon). Ordered work follows
[the implementation plan](../../docs/plans/MM_REPORTING_STARTUP_OPTIMIZATION_TDD_PLAN.md).
Final image/load verification is pending until all source phases finish.

## A — Daily request admission

The previous global lock made an uncached spot report wait for an unrelated
perpetual aggregate. Cancellation could release that lock before the physical
query ended, allowing a new poll to duplicate the same work. Three Red cases
reproduce independent-key blocking, canceled-leader duplication and missing
bounded key admission (three failures/four existing passes).

With caching enabled, canonical timezone/date/market/pair keys now share a
shielded in-flight refresh. Different keys use the existing database scheduler,
with its two workers, bounded queue and control priority. The registry has at
most12 outstanding keys, removed on success/failure/cancellation/shutdown. Full
capacity uses the existing503/unavailable behavior or a qualified last-good
fallback. The cache remains32 entries; TTL0 bypasses sharing and fallback.
No new worker, SQL connection ceiling, Redis, schema or trading cache is added.

Midnight regressions cover queued failures and relative/explicit requests that
initially shared one resolved range. A caller re-resolves after shared work and
performs a bounded fresh read if its range changed. Clock disagreement cannot
create an endless retry loop. Original observation times, fresh/stale labels,
60s fallback age, cancellation propagation and permanent-error handling remain.

Validation: **61 focused**, **889 CLI** and **23 PostgreSQL** tests pass
(256 PG cases deselected for this phase). Tests cover two uncached markets,
same-key cancellation,12-key admission,100 sequential keys with no registry
retention, shutdown retaining physical worker ownership, TTL0 and midnight.
Existing SQLAlchemy/Pydantic deprecation warnings remain. The initial test import
and one test filename were corrected before the retained Red/Green runs; their
launcher logs are preserved separately. These are source results, not image or
load acceptance.

Raw logs: `build/reporting-startup-20261102/a-*.log`.

## B — Historical startup

Measurement pending after phase A commit. Preserve streamed history, exact
Float-to-Decimal accumulation and cursor cleanup; no persistent aggregate is
assumed necessary.

## C — Daily SQL scaling

Review pending. Earlier workload-dependent SQL regressions and the cold
million-fill limit remain recorded in the
[SQL investigation](../investigations/mm-reporting-latency.md).

## Final image and load

Pending. Freeze the completed application/measurement source, then run one full
R1 and matched ordinary-entrypoint R2. Preserve failures and historical
attribution limits; no threshold or safety gate is relaxed.
