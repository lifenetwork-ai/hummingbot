# Pre-window owner publication timeout

2026-11-04. The historical timeout and restart are confirmed. Three new
preparation runs do not reproduce them. One traced run establishes a separate
1.182s publication COMMIT wait on PostgreSQL WAL I/O during historical fixture
insertion. This is a concrete storage-pressure lead, **not proof of the cause
of the earlier SELECT timeout**. Historical attribution remains open.

[Evidence](mm-owner-publication-timeout-evidence.json),
[investigation plan](../../docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md).

## Historical sequence

Source `0f4095671`, image `086454de`, raw artifact
`build/mm-load/20261104-134831-d0dea6/`. All times below are UTC.

| Time | Observation |
| --- | --- |
| 06:48:53.513–53.516 | Initial spot strategy and clock finish stopping. |
| 06:48:59.915 | PostgreSQL cancels the initial `BotStateStore.publish()` SELECT on statement timeout. |
| 06:48:59.921 | Engine reports `critical_persistence_failure`, then exits with code 1. |
| 06:49:00.954 | Supervisor starts a replacement engine. |
| 06:49:06.543 | Replacement bootstrap completes. |
| 06:49:33.009 | The passing measured hour begins. |

The failed statement selects `bot_state.status, bot_state.mm_session FOR UPDATE`
with bot ID, owner UUID, owner generation, control version 2 and acknowledgement
predicates. Failure occurs before its status/heartbeat update. This is a real
engine exit in the offline preparation phase, after the initial Stop. It is not
an OKX REST/WS timeout: this fixture uses the existing simulator on an internal
Docker network. It also is not a failure of the optional daily-report query.

The original artifact has no contemporaneous pre-window PG wait/blocker sample
or runtime stack. Timed-window sampling starts later. The passing hour therefore
cannot explain or erase the preparation failure.

## Repeated original preparation

Reuse the same image, original `test_mm_load.py` preparation, default 100,000
history rows, two-CPU bot/two-CPU PostgreSQL limits, enhancements and volatility
defense. Preserve the initial spot Start/Stop, verified outflow origin, bulk
history insertion and subsequent both-market Start/Switch/Stop/Start sequence.
Stop at the original `run_window()` boundary; no new load acceptance is claimed.

One bounded diagnostic connection observes `pg_stat_activity`, blocking PIDs
and relevant `pg_locks` from before engine startup through preparation. Reuse
the existing SQL-template sanitizer and runtime tracer. No query parameters,
credentials or frame locals are retained. The raw runner and artifact hashes
are listed in the evidence JSON.

| Case | Runtime trace | PG samples | Largest sample gap | History insertion | Persistence errors / engine starts |
| --- | --- | ---: | ---: | ---: | ---: |
| 4 | On | 660 | 230.49ms | 10.08s | 0 / 1 |
| 5 | Off | 648 | 252.34ms | 8.12s | 0 / 1 |
| 6 | Off | 528 | 206.92ms | 8.07s | 0 / 1 |

None of the 1,836 retained samples observes a backend blocked by another PID.
This is sampled evidence, not a guarantee that every short lock wait was absent.
All three preparations complete without a PostgreSQL error or engine restart.
The earlier diagnostic attempts are retained separately: a missing published
port, an interrupted repeat and an incomplete multiline-JSON parser run. They
are excluded from this table. Case 4's Docker-exec cancellation message is the
intentional sampler shutdown, not a server error.

## Concrete WAL observation

In case 4, at 08:59:04.961 UTC, engine backend PID 140 commits the transaction
containing the fenced publication SELECT, heartbeat/status UPDATE and command
observation. Its driver COMMIT span lasts **1,182.32ms**, with 0.379ms inclusive
thread CPU. Eleven samples of that same PID, during that same span, show an
active COMMIT waiting on `IO/WALWrite`, `IO/WALInitWrite`, then `IO/WALInitSync`;
blocking-PID lists are empty. The history insertion is in progress. The preceding
publication SELECT in this transaction takes 2.32ms and completes successfully.

The wait names identify WAL writing, initialization and synchronization in
[PostgreSQL's monitoring reference](https://www.postgresql.org/docs/14/monitoring-stats.html#MONITORING-PG-STAT-ACTIVITY-VIEW).
This demonstrates a publication transaction can encounter storage latency
without another transaction holding its row. PostgreSQL also documents that
[locking a row with SELECT FOR UPDATE can cause disk writes](https://www.postgresql.org/docs/14/explicit-locking.html#LOCKING-ROWS).
Storage/WAL pressure is consequently a plausible hypothesis for investigation;
this successful COMMIT is a different operation/run from the historical failed
SELECT. Neither this correlation nor its low thread CPU establishes exclusive
historical causality, the physical device at fault, or absence of scheduling
pressure.

## Controlled PostgreSQL checks

Three new real-PG tests, plus eleven related control tests, pass unchanged
runtime budgets: statement timeout 2s, lock timeout 1s and I/O response deadline
3s. Run with the Conda project interpreter:

```sh
python scripts/test_postgres.py -k 'publication_lock_timeout_has_blocker or unrelated_row_lock_does or statement_timeout_is_distinct or test_control'
```

- Holding the bot's row reproduces a witnessed lock wait and SQLSTATE `55P03`.
  The failed publish leaves acknowledgement, heartbeat and status unchanged.
  After releasing the blocker, a separate healthy invocation succeeds.
- Holding an unrelated fixture row allows publication to succeed.
- An intentionally long statement produces `57014`, rolls back and leaves its
  physical connection usable.

The first test draft incorrectly tried to create a second bot identity; the
existing identity guard rejected that fixture. The corrected negative control
uses an unrelated table. This was not a runtime Red/Green fix. The controlled
single-lock case produces a different error from the original statement timeout;
it does not rule out more complex contention or delayed timeout processing in
the unobserved incident.

## Decision and remaining evidence

No runtime defect or safe behavioral fix has been established by this phase.
Keep the current fail-fast/recovery behavior and ownership/version fences.
Do not add blind retries, skip the row lock, raise deadlines, increase pool size
or suppress the persistence error on this evidence. The replacement process
must continue through ownership acquisition and recovery rather than assume
the failed publication committed.

For the next occurrence, retain PG activity/waits/blockers **before the measured
window**, correlate the publication backend with engine/API transactions and
stacks, and check WAL/I/O plus scheduling pressure. A failed SELECT sampled in
WAL I/O supports a different fix from one blocked by a long application
transaction. Existing post-window-only evidence cannot recover those missing
facts retroactively. The original root-cause item remains open; repeated clean
preparations are not a verified fix.

Actual Demo remains intentionally stopped, owner available, no HALT/pending
mutations or unresolved orders. All four order/fill/position/executor hashes
match the prior baseline, including all 23 fills. No exchange orders, recovery
resets, image changes or production deployment were performed.
