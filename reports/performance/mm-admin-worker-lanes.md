# Admin database worker lanes

2026-11-04 (Asia/Saigon). Source implementation and focused TDD verification are
in progress. Image/load acceptance is pending; the preceding f4d90bc7 hour and
failed short p99 retain their original scopes.

[Plan](../../docs/plans/MM_ADMIN_WORKER_LANES_TDD_PLAN.md),
[evidence](mm-admin-worker-lanes-evidence.json).

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
Heavy requests can wait longer and peak history throughput may fall; no numerical
latency or throughput improvement is claimed before measurement. A small history
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
omits required fields and returns422. Correct the fixture and retain that log;
it is excluded from runtime Red proof. The corrected focused PostgreSQL run
passes five cases, including balances/Stop/receipt while a large-history query
holds a real connection, owner capacity and unchanged daily retry semantics.
Broad source and packaged/load checks are pending.

Final source checks: native2,494 +11 subtests, PG355 and Swagger pass.
Related native78 checks include30 new cases. Image/load acceptance is pending.
