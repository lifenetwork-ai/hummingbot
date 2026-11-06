# Current-image probe code audit

Reviewed on 2026-11-07 (Asia/Saigon). Runtime `2f6cbd257`, image `f74d2a2a…`,
unchanged since the completed [full load](../release/admin-cleanup-state-load.md).
This investigation reads production code, reanalyzes the six retained soak
episodes and reruns focused existing tests. No runtime change, new image, load
rerun, real exchange operation or Demo change is made.

## Finding

The health handler itself is small and performs no DB, KMS or OKX request. It
shares the engine event loop with synchronous ownership/recorder work. A slow
ownership read, checkpoint, fill transaction or sequence of callbacks can delay
handling the HTTP request. Psycopg's bounded socket wait releases the GIL but
blocks its calling thread; it does not yield this asyncio event loop.

The current trace contains a **335.89 ms synchronous recorder callback**, with
the engine-loop stack in checkpoint SQL while a probe times out. This is a
concrete blocking-path lead, beyond merely speculating about the network or
web framework. It does not establish that one path exclusively causes all six
episodes. No newly reproduced correctness defect is established.

## Code paths checked

| Path | What it does | Implication |
| --- | --- | --- |
| `ServiceHost.health()` in `hummingbot/cli/service.py` | Checks stop/bootstrap/readiness/observer freshness and returns a small JSON body | `/live` does not query SQL or call OKX; failure reason in this run is timeout, not HTTP 503 |
| `create_service_app()` / `run_service()` | Registers health and runs `host.run()` on the same loop | HTTP handling can wait behind other synchronous engine work |
| `admin_middleware()` | Immediately dispatches non-admin routes | Google verification and API DB-worker admission are absent from this engine health path |
| `probe_health()` / `run_supervisor()` | Loopback HTTP, 300 ms socket timeout, another poll after approximately one second | A roughly one-second failure/recovery transition is not a measured one-second handler execution or quote gap |
| `HealthWatchdog.observe()` | Warns on first failure, resets on success; restarts only after sustained failure grace | Six recovered episodes do not themselves restart the engine |
| `BotOwnership.check_owner()` | Synchronous SQL/commit on the dedicated ownership connection | Mandatory live authority checks can hold the loop during DB I/O; they are not safe to replace with a cached boolean |
| `before_order()` → `record_order_intent()` → `save_market_states()` | Persists intent/tracking before transport and preserves unresolved identities | This is durable send/recovery protection, not optional reporting |
| `_did_fill_order()` → `_store_fill()` → `save_market_states()` | Synchronous recorder/checkpoint transaction on the loop | Matches the retained stall stack in one measured timeout |
| `bounded_wait()` in `postgres/io_deadline.py` | Calls `select.select()` synchronously while waiting for PostgreSQL | Socket waiting blocks the calling loop thread even if another Python thread can run |
| `publish_status()` | Detaches a loop-owned snapshot, sends DB publication through `asyncio.to_thread()` | Slow publication SQL is already isolated; JSON/projection construction remains synchronous |

## Exact six episodes

Times below are UTC on 2026-11-06. Observation intervals come from supervisor
failure/recovery markers; failed-attempt timings are client-side socket timings.
The context includes up to one second before failure. Nested/overlapping span
durations must not be added together.

| Failure UTC | Failed attempt | Observation interval | Retained context and attribution limit |
| --- | ---: | ---: | --- |
| 14:04:32.476 | 317.219 ms | 1.074 s | Recorder callback 335.89 ms, fill storage 261.99 ms. At failure+8.45 ms, loop-stall sample 220.76 ms shows `_store_fill` → checkpoint read → `bounded_wait`. Strong direct blocking-path lead; not exclusive attribution. |
| 14:05:01.026 | 303.781 ms | 1.092 s | Observer root 599.58 ms overlaps the transition; GC 59.38 ms also overlaps. Earlier recorder/intent stalls 347.23/593.05 ms occur before failure, not during it. The broad observer root includes awaits/interleaved work. |
| 14:21:26.296 | 355.734 ms | 1.239 s | Observer 307.64 ms and executor reports 117.54 ms overlap. Ownership read 76.49 ms / SQL 60.05 ms are retained. No stall stack uniquely identifies the timeout cause. |
| 14:23:00.041 | 301.106 ms | 1.048 s | Observer 160.60 ms overlaps; retained SQL/pool spans are small. Context cannot explain the whole request wait. |
| 14:29:13.650 | 318.692 ms | 1.104 s | Observer 330.46 ms, recorder 168.56 ms and checkpoint 90.89 ms overlap. A 10.20 ms GC is preceding context. The observer duration is not exclusive loop occupancy. |
| 14:32:10.681 | 301.246 ms | 1.034 s | Observer 140.79 ms overlaps; small retained ownership/pool spans. No complete scheduling/handler timeline. |

All six recover inside soak. The original healthy-window no-restart, settlement
and API results remain unchanged. End-of-window incident summaries show engine 18/
API 8 captures, zero suppressed incidents and zero oversize records. This run did
not exhaust the 32-capture count. Context remains bounded: engine 76,151/API 7,579
omitted spans are explicitly retained. Missing spans/stacks are not proof of an
idle loop, CPU starvation, a specific SQL bottleneck or an external OKX problem.

Async root wall time and thread CPU can include other coroutine activity while
awaiting. No per-coroutine CPU claim or new MM continuity claim follows from these
six observer spans. The local HTTP path has no direct OKX dependency; concurrent
connector event processing can still affect loop scheduling.

## Focused verification

- **30 native tests pass**: publication cancellation/shutdown fencing, fresh
  observer authority and supervisor recovery policy.
- **Two PostgreSQL tests pass**: exactly two required stopped-observer ownership
  reads, retained authority checks, and a real 300 ms HTTP probe under controlled
  120 ms delays per ownership read.
- **One separate PostgreSQL test passes**: a 400 ms publication SELECT runs in the
  existing worker while the private health request returns 200 under its 300 ms
  timeout. This isolates publication SQL, not all synchronous recorder work.

These are existing regression cases rerun against disposable PostgreSQL, not new
Red/Green evidence of a production fix. The initial PG selector matched only two
cases; the publication case was then explicitly selected and passed separately.
All logs, source/trace hashes, exact probe markers and scoped stack/span evidence
are retained in the [portable evidence](mm-cleanup-state-probe-code-audit-evidence.json).

## Engineering decision

Keep the accepted runtime and health policy unchanged. Moving `/live` to an
always-responsive thread could hide a stalled trading loop. Increasing its timeout
would not remove the synchronous work. Moving a recorder/owner method wholesale
to `to_thread()` could race loop-owned connector state, dedicated DB access,
transaction ordering or the durable-before-send fence.

If another optimization phase is undertaken, first split timing inside the
existing observer snapshot and recorder path: ownership read, session validation,
report projection/JSON, tracking-state encoding, checkpoint lookup/merge and
commit. Reuse the bounded opt-in tracing primitives. Measure payload/query counts
before choosing a narrow projection/batch correction. Any confirmed correction
needs Red/Green authority, pending-intent, late-fill, commit-failure and restart
tests before new image qualification. No speculative cache, queue or framework
replacement is justified by this audit.
