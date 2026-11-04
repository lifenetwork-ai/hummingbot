# Pre-window owner publication timeout

2026-11-04. **Follow-up reproduces the same failing publication SELECT and
SQLSTATE under controlled WAL I/O pressure.** The historical exclusive trigger
remains unproven; the causal mechanism is now demonstrated below. The offline
fixture preparation correction and its subsequent standalone-hour confirmation
are verified in the final sections. Eight recovered slow probes remain explicit.

The initial three preparation runs do not reproduce the failure. One establishes a separate
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

## Follow-up: matched WAL-pressure reproduction

[Follow-up evidence](mm-owner-publication-wal-followup-evidence.json) retains all
ordinary runs, imposed faults and unsuccessful interventions separately.

Seven further original-prefix runs, alternating three traced/four untraced,
complete with no persistence failure/restart and 3,251 PG samples. An additional
diagnostic variant changes deadlock detection to 200ms and is excluded from that
unchanged-setting set. Increasing history to 500,000 rows also completes without
a persistence failure: its final cumulative WAL snapshot is about 1.07GB.
These results do not make the old failure disappear.

Limit writes to **8 MiB/s in only the disposable PG container's cgroup**, keeping
the same bot image, application deadlines, durability settings, first spot
Start/Stop and 100,000-row fixture. Do not change host-wide device limits.

| Case | Intervention | Observed outcome |
| --- | --- | --- |
| 17 | 8 MiB/s, default WAL initialization | Publication lock timeout `55P03`; engine restarts. Fixture command subsequently exceeds its original 60s Docker budget. |
| 18 | Repeat; retain traces across restart; fixture-only budget 180s | Same publication lock timeout; blocker COMMIT waits on `IO/WALInitSync`. |
| 19 | Repeat with a guarded optional stack-dump handler | **Same publication SELECT/control version 2 fails with `57014`**; engine restarts. |
| 20 | Same I/O limit, `wal_init_zero=off` | Publication SELECT exceeds the client's 3s response deadline; engine restarts. This setting is not a verified fix. |
| 21 | Same I/O limit, zero historical rows | Completes without persistence failure or restart. |
| 22 | Same no-bulk control, observe another 65s after seed | Completes without persistence failure or restart; covers the roughly one-minute bulk-seed interval of cases 18/19. |

In case 19, backend **322** starts the fenced publication SELECT at
09:42:43.352 UTC. Fifteen same-PID samples show `LWLock/WALWrite`, empty blocking
PID lists and observed statement ages up to 2.817s. At 09:42:46.250 UTC PostgreSQL
logs `57014` for that exact SELECT. Its retained runtime SQL span lasts
**2,901.57ms**, with 2.693ms inclusive thread CPU and `QueryCanceled`; the engine
reports `critical_persistence_failure` at 09:42:46.459 UTC. A configured statement
timeout is not a guarantee of cancellation at exactly 2,000ms in every server
wait. The separate client response bound is unchanged.

Cases 17/18 demonstrate another consequence of the same imposed write pressure:
a transaction waiting for WAL durability retains its row lock, and a concurrent
publication reaches the 1s lock timeout. The blocker is witnessed at COMMIT;
its originating application operation is not established by the retained
completed spans. Do not attribute it to funding or another specific handler.
Subsequent bootstrap retries during the still-running bulk transaction also
encounter migration/DDL contention; the evidence retains these failures.

This establishes a causal **bulk-write/WAL-pressure failure mechanism** for the
unchanged engine. It does not prove the historical host experienced an 8 MiB/s
limit, identify its original storage/scheduling bottleneck, or establish that
every publication timeout has this cause. Collector queries have a separate
200ms limit and repeatedly time out/reconnect under the imposed fault. Their
gaps and errors are retained and never counted as runtime publication failures.

Historical Docker VM logs add a PG-container exec from 06:48:54.200 through
06:49:02.234 UTC, overlapping the original 06:48:59.915 timeout. Harness call
ordering identifies bulk seeding as the likely command; its request body was
not archived. Host power records contain no Sleep/Wake transition in the local
13:45–13:52 window and show an active sleep inhibitor. This is not evidence
excluding shorter CPU, VM or storage stalls.

## Confirmed diagnostic defect and TDD correction

The full-runtime trace writer opened `server.jsonl` / `server-api.jsonl` in
overwrite mode when a child restarted. Case 17 consequently lost its first
engine's trace after the supervisor restart. This is an offline instrumentation
bug, separate from the engine's persistence failure.

Commit `3a4737a44` uses the existing bounded append mode through the runtime
trace factory. Three real OS-process restart tests first reproduce loss of the
first record; all **58 related trace/analysis/timing/incident tests** pass after
the correction. Cumulative file-byte ceilings and tracing opt-in remain intact.
Cases 18–21 retain pre-failure SQL spans after restart. This does not guarantee
every background/cleanup span was flushed before its process exited; collector
gaps remain explicit. No shipping engine code/image changed.

## Recommended action

Treat this mechanism as shared database write pressure, not an API-framework,
cache, or connection-pool problem. Separate large fixture seeds and manual bulk
imports from a running service, then measure WAL/commit latency on the intended
database storage. Stopping only the MM session leaves the service publishing
heartbeats and observational state. A controlled full service stop is needed
for an offline import; normal startup/recovery checks must run on restart.

Keep the durability settings, ownership fences and critical-persistence failure
handling. The `wal_init_zero=off` control failed and is not recommended as the
remedy. No new release acceptance or production database setting is claimed.
Historical exclusive attribution stays open even though the matching mechanism
has been reproduced. Final read-only verification preserves stopped Demo,
23 fills, all four ledger hashes and no recovery block.


## Offline history preparation correction

Commits `7bdb5b6f6` and `2b4011556` move bulk fixture insertion out of the
running service. Stopping a trading session alone had left the owner publisher
active while the fixture generated WAL. The shared
`scripts/mm_load_preparation.py` now requires acknowledged Stop, clean full
container shutdown and released ownership before inserting history. It checks
for remaining application DB connections, owner locks and pending orders,
seeds the original rows, and runs CHECKPOINT before normal service startup.
Startup must acquire a new owner generation with stopped intent, matching
control version, no recovery block and preserved outflow origin. Failed seed
or CHECKPOINT leaves the test service offline; unexpected child failures or
restarts prevent workload admission. There is no recovery/HALT reset.

All six maintained callers share this preparation: main load, reporting
isolation, production control, random handover, memory and process-isolation
diagnostics. Memory reads wait for fresh post-restart metrics; process-fault
checks capture their initial generation after the planned setup restart.

Red/Green coverage includes 13 initial failures, one lost-outflow-origin case
and six caller-wiring cases. **169 related native tests and seven PostgreSQL
fixture/reporting compatibility tests pass.** The first caller Green invocation
referenced an absent optional test file and ran no tests; its corrected final
invocation supplies the 169 passing results. Raw attempts remain retained.

| Verification | Result |
| --- | --- |
| Two 8 MiB/s PG controls | Each seeds 100,000 orders and 100,000 fills offline; application connections/owner locks/pending orders are all zero. Generation 1→2, then 65s stable with no publication failure or unexpected child restart. |
| Unchanged 300s load | PASS; 6,600 measured HTTP 200 responses, no unexpected errors/stale; primary p95/p99 50.83/113.94ms, auxiliary p95/p99 29.22/36.87ms; 300 exact synthetic fills/fees/positions. |
| Controlled slow-report, 120s | PASS; one intentional `57014`, retry returns fresh data, zero stale replies, 120 exact synthetic fills/fees/positions; engine PID, owner and session unchanged, no HALT. Two simulator fills occur during the timed-out query, with one durable-fill witness inside it. |
| Actual Demo restoration | Stopped, owner available, 23 existing fills; order/fill/position/executor counts and all four hashes unchanged. |

The 300s load retains two single engine-probe timeout/recovery episodes: next
successful observations arrive about 1.03–1.04s after the initial failed probe;
maximum recorded probe elapsed is 303.64ms. There is no unexpected service
failure or child restart. These observations do not establish that MM paused
for the supervisor's observation interval, and this phase does not claim to
eliminate every short slow probe.

The 8 MiB/s controls validate **offline preparation and stopped-session startup**;
they do not qualify live trading under that storage limit. The diagnostic PG
sampler has independent 200ms timeouts/reconnects during the imposed pressure;
these remain in the evidence. Initial Stop also produces expected local
`SubmissionBlocked` records, which are not venue submissions or publication
failures. No claim of entirely error-free raw logs is made.

Shipping image remains `086454de`; its ten recorded runtime source hashes still
match. Runtime ownership fences, durability, 2s statement/1s lock/3s client I/O
deadlines, workload rates, history timestamps/outflow expiry and acceptance
budgets are unchanged. Only the administrative fixture seed/CHECKPOINT command
gets a 180s Docker budget while the service is offline. A planned full restart
before measurement is recorded separately from an unexpected runtime restart.

The main load and slow-report paths ran with real disposable containers. The
other migrated long suites have common-lifecycle and wiring coverage; they were
not fully rerun here. No new sixty-minute soak, real Demo trade, image rebuild
or deployment was performed. Earlier failed artifacts and the old measured hour
retain their original scopes; exclusive attribution of the original historical
trigger remains open.

[Machine-readable evidence](mm-offline-history-preparation-evidence.json) records
source/image fingerprints, raw artifact hashes, preparation stages, controlled
failures, measured results and read-only Demo preservation checks.


## Full-hour confirmation after offline preparation

Requested standalone Random soak completed on the unchanged `086454de` image,
2026-11-04 **10:47:14.268–11:47:14.268 UTC**. Raw result:
`build/mm-load/20261104-174603-b267d6/`; isolated-run/restoration artifacts:
`build/mm-offline-history-soak-20261104T104559Z/`. Source remains `581485151`
with the two committed host-side preparation changes; no shipping code changed.

| Check | Result |
| --- | --- |
| Preparation | Full service offline, no app connection/owner lock/pending order; 100,000 orders + 100,000 fills, CHECKPOINT, normal restart and owner generation 1→2. No publication failure or unexpected restart. |
| Duration and continuity | 3,600s; 58 minute samples, maximum gap 65.50s; sampled engine PID stays 32 and every sample reports running. |
| HTTP availability | 72,000 primary + 7,200 auxiliary HTTP 200 replies, zero unexpected errors/stale. |
| Latency | Primary p95/p99 58.13/260.39ms; auxiliary p95/p99 35.22/77.27ms. |
| Ledger | 3,595 unique simulator fills match recorder quantity/original fees and positions; two identified funding bills, three daily snapshots. |
| Random coverage | Six steps cover sideways, follow_token and follow_market. Random fill rate retains its original observational scope; no post-window fill catch-up. |
| Memory retention | Early/late median 700,522,496/730,042,368 bytes; +28.15MiB, below unchanged 64MiB growth budget. This bounds retention for this hour, not indefinitely. |
| Demo restoration | Stopped, owner available, no HALT/pending mutation/unresolved order; all 23 fills, four table counts and four hashes unchanged. |

The workload/resource/deadline/guard/cache settings match the previous standalone
hour. No unexpected `service_failed` or `child_restart_scheduled` event appears
in the entire retained bot log, including preparation. Twelve ERROR records are
six locally blocked order IDs logged by connector/executor during the initial
pre-window Stop; none exists in the simulator's accepted-order ledger. They are
not measured API errors or publication failures and remain in the evidence.

### What the slow probes do and do not establish

**Eight engine-probe timeout episodes recover without restart.** Seven have one
failed probe each; one has three consecutive failures. The longest supervisor
observation interval is **3.798s**; maximum reported probe elapsed is
**338.964ms**, against the unchanged 300ms probe limit. The passing soak does not
close residual-probe latency or establish a unique cause.

We correlate each supervisor log timestamp minus its reported observation
duration with existing engine logs and independently retained simulator ledger
timestamps. All eight derived intervals contain local fill-processing logs
and independent simulator fills; **seven also contain newly accepted orders**.
In the 3.798s interval specifically, there are five local fill-processing logs,
five independent simulator fills and twelve independently accepted new orders.
This demonstrates that MM performs some work inside that interval rather than
remaining stopped throughout it. These counts are different event types, not a
single count of unique fills or completed round trips.

The joins use wall-clock event records and rounded supervisor durations; they
are not aligned per-tick spans. Local processing may lag the simulator event.
Existing quotes can fill while an engine is stalled, so simulator fills alone
would be insufficient; the local processing/new-acceptance witnesses add
separate evidence. They still do **not** prove every tick/controller deadline,
full submit/cancel RTT, continuous fresh two-sided quotes or actual OKX behavior.
The unchanged hour did not enable `--mm-activity`. The existing bounded,
opt-in activity/independent-venue diagnostic is the appropriate separate test
for stronger per-cycle claims; no production instrumentation is added here.

The full-hour checklist is closed for this preparation correction. Prior failed
attempts and measured windows retain their original scopes. `release_scope_complete`
remains false: no new full R1/R2/fault suite, actual Demo trade, shipping-image
rebuild, production deployment or exclusive historical-trigger attribution.

[Portable evidence](../performance/mm-offline-history-soak-evidence.json) includes
preparation stages, unchanged settings/source fingerprints, continuity, all eight
probe intervals, local/independent simulator witnesses and raw artifact hashes.


## Subsequent per-cycle MM/probe measurement

The same-image bounded600s [MM/probe diagnostic](mm-current-probe-correlation.md)
uses the existing activity/independent-venue instruments. Its single measured
probe318.874ms recovers while tick/controller progress and both-market two-sided
quotes remain observed. Whole-window controller jitter, replacement/guard gaps
and repeated spot SELL invalidation remain visible. This adds direct evidence
for that captured case, not per-tick reconstruction of the eight prior Random
incidents or closure of historical unique cause. No shipping code/settings change.
