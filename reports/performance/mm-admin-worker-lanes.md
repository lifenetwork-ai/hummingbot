# Admin database worker lanes

2026-11-04 (Asia/Saigon). **Scoped image verification passes** on `086454de`
from source `0f4095671`: native2,494 +11 subtests, PG355, Swagger, three packaged
E2E groups, four controlled reporting faults, the unchanged300s probe and the
standalone hour. The hour has79,200 HTTP200 replies, no errors/stale, primary/
auxiliary p99 246.24/88.55ms and3,595 matched synthetic fills/fees/positions.
One engine persistence timeout/restart occurs **before** the timed window and
remains an explicitly bounded observation below. No full R1/R2 or production
acceptance is transferred. The preceding f4 failures remain failed.

[Plan](../../docs/plans/MM_ADMIN_WORKER_LANES_TDD_PLAN.md),
[evidence](mm-admin-worker-lanes-evidence.json).

Follow-up: [pre-window owner publication investigation](../investigations/mm-owner-publication-timeout.md)
adds three clean preparations and a witnessed 1.182s WAL-delayed publication
COMMIT. The original failed SELECT remains unattributed; no runtime fix is
claimed.

## Change and tradeoff

The existing two-thread API database executor admits at most one operations job
and one heavy job concurrently. Commands retain priority over queued operations;
running physical calls cannot be preempted. Heavy FIFO progress does not depend
on an operations/control stream draining. Total pending32 and non-control
pending12 remain; heavy pending is capped at10 to reserve two read-admission
slots for operations. Canceled awaiters never release a still-running job.

Daily statistics and history pages/timelines above100 rows use the heavy lane.
Default-sized pages up to100, pinned session configuration and current-state
reads remain operational. This threshold matches the existing default page size.
Heavy requests can wait longer and peak history throughput may fall. A small history
query can still delay its own operations lane; shared DB/CPU pressure remains.

No SQL, schema, connection ceiling, statement/I/O deadline, cache TTL, API payload,
trading engine or ownership logic is changed. Test-only tracing forwards the lane
classification and labels physical admission without changing scheduling.

## TDD and checks

The initial native Red has six failures, including the actual daily/history/
balance handler scenario where two heavy jobs block the balance read. Further
Red tests expose missing operational admission reservation and an intermediate
all-history classification that needlessly blocks default-sized pages. These
are retained development results, not deployed incidents. The final candidate
reserves admission and keeps small pages operational.

Existing capacity/cancellation tests now hold one physical job from each lane.
TTL0 still performs distinct observations, serialized in the heavy lane rather
than coalesced. Routing tests cover default and large page/timeline sizes.
Additional cases cover heavy FIFO progress, control priority, canceled running/
queued work, worker submission failure, exceptions and shutdown/drain behavior.

The first related PostgreSQL attempt has an invalid synthetic Stop receipt that
omits required fields and returns422. The fixture is corrected and that log
retained separately; it is excluded from runtime Red proof. The corrected focused PostgreSQL run
passes five cases, including balances/Stop/receipt while a large-history query
holds a real connection, owner capacity and unchanged daily retry semantics.
Final source checks: native2,494 +11 subtests, PG355 and Swagger pass.
Related native78 checks include30 new cases; one new real-PG case exercises
the HTTP/history/Stop connection reservation. The rebuilt image independently
matches ten runtime hashes and all197 preceding Python package versions.

## Image and controlled faults

Packaged life-MM, recovery and volatility-defense groups pass on the same
immutable image. Four120s wait/CPU cases independently verify progress:
one actual2s statement timeout recovers a fresh report with zero stale replies;
two exhaust the bounded retry and produce18 expected stale replies per case.
Each witnesses a committed fill during the first timeout, reconciles120 unique
fills/fees/positions, retains engine PID/owner/session, has no HALT, later returns
fresh statistics and stops cleanly. No runtime-source overlay or extra worker
is used. Expected stale responses in impaired cases are not healthy-gate passes.

## Unchanged short and hour measurements

Both runs keep100k historical orders/fills, two application CPUs/2GiB, two PG
CPUs/512MiB, pool5, TTL15,20 primary +2 auxiliary reads/s and all enabled defense
fixtures. The300s probe returns6,600 HTTP200 with no error/stale, matches300
synthetic fills and passes every original budget. The3,600s hour returns79,200
HTTP200 with no error/stale and matches3,595 synthetic fills, fees and positions.
Random visits all three scenarios; gaps during step admission remain observed
in the original soak rate scope rather than being relabelled as fixed-rate proof.

| Metric, p99 ms | Prior f4 short | New short | Prior f4 hour | New hour |
| --- | ---: | ---: | ---: | ---: |
| Primary reads |1,094.27|313.92|615.65|246.24|
| Auxiliary reads |1,112.11|69.26|565.42|88.55|
| Status |966.66|31.75|471.04|44.96|
| Balances |1,105.58|61.75|622.48|56.21|
| Positions |1,098.35|65.25|593.53|41.02|
| Orders,500 rows |1,250.14|448.00|746.04|385.53|
| Daily statistics |1,184.16|644.54|731.63|580.58|

These are sequential measurements on a changing emulated host, not randomized
paired causal benchmarks or a Django/framework comparison. The old failed short
result is retained as failed. Direct Red/Green job-holding tests establish the
lane reservation's causal behavior; these wall-time differences alone do not
identify an exclusive cause of the earlier p99 spike.

The hour's retained-memory growth is25.96MiB against64MiB, with697.69MiB raw
cgroup peak against1.5GiB. Its58 runtime samples have maximum65.50s spacing;
per-role RSS, FD, thread, PID, backlog and pool-retention checks pass. Supervisor/
engine/API PIDs remain1/218/33 throughout the measured window. Ten engine probe
episodes recover, maximum logged observation interval1.251s; no API probe failure
is logged in that window. No hour report timeout/retry occurs. Two WARNING
SubmissionBlocked records describe one locally denied order during joint
readiness recovery; no ERROR/CRITICAL is logged within the timed window.
These observations do not prove uninterrupted quoting or every long-term leak's
absence. The four controlled cases establish timeout recovery behavior separately.

## Pre-window persistence observation — cause not closed

During setup, before the hour starts, PostgreSQL cancels an owner-fenced
`SELECT bot_state.status, bot_state.mm_session ... FOR UPDATE` at its2s deadline
(06:48:59.915 UTC). The engine logs `critical_persistence_failure`, exits and is
restarted by the existing supervisor at06:49:00.954 UTC. Its new generation
bootstraps by06:49:06.543 UTC; the timed window starts at06:49:33.009 UTC.
The API process remains running. The start/Stop/seed preparation also contains
locally blocked submissions; these pre-window errors are not silently removed
or counted as measured healthy-window errors.

This establishes the timed-out statement and automatic recovery, not the
blocking transaction, exact wait category or unique trigger. No blocking-PID
trace is retained for that interval. It must not be attributed exclusively to
fixture seeding, the new lanes, OKX or a source-level bug from temporal overlap.
Engine/store persistence code and deadlines are unchanged. No fail-fast/fencing
behavior is weakened. A future startup-availability claim needs bounded
transaction/lock/scheduling capture on recurrence; the passing hour does not
retroactively make the entire preparation restart-free.

## Restored state and scope

Stopped Demo upgrade/restart and post-soak read-only REST/public/privateWS
preflight pass. A private pre-soak dump is restored into a network-isolated
disposable PG clone; orders/fills/positions/executors hashes match the actual
Demo database. All23 existing Demo fills and the original private YAML/env remain
unchanged. Canonical Demo is running image086454de with trading stopped,
owner available, no HALT/pending mutation/unresolved order. Ten current source/
container runtime hashes match. No active connected-Demo session, new natural
fill, database repair, Cloud Run deployment or full R1/R2 is performed here.
