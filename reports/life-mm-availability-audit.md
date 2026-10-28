# LIFE MM service availability audit

Date: 2026-10-28 (client timezone Asia/Saigon). Audited baseline: `06580fbcf`;
application source: `f52f6ee3f`, previously accepted image `c924030a…`.
Status: audit complete; **three P1 and two P2 findings remain open**.
[Portable evidence and reproducer sources](life-mm-availability-audit-evidence.json).

This review did not change runtime code, send exchange requests, start the Demo
bot or deploy Cloud Run. It examined the service/owner lifecycle, final REST
send gate, DB publication, Admin API scheduling and controller action delivery.
Five controlled probes reproduce the findings against a disposable local
PostgreSQL and synthetic lifecycle objects. The persistent Demo DB is untouched.

The earlier healthy 60-minute/90k-read and bounded Demo acceptance remains valid
for its declared workload. It did not cover these adversarial cases and does not
close them. No spontaneous production incident is claimed.

| ID | Priority | Trigger | Consequence |
|---|---|---|---|
| AUD-001 | P1 | SIGTERM/error cleanup awaits optional tasks | New order authorization remains possible during shutdown |
| AUD-002 | P1 | Lock timeout on an optional daily balance row | Exception reaches the service exit boundary despite a healthy owner |
| AUD-003 | P1 | DB response lost after an established connection | Main-loop synchronous DB call can outwait server timeout; API/health/Stop become unresponsive |
| AUD-004 | P2 | Seven valid slow reads followed by Stop | Stop acceptance waits 5.428 s before application starts |
| AUD-005 | P2 | Required action-consumer task is unexpectedly canceled | Runtime gate still reports healthy while a cancellation batch remains unconsumed |

P1 denotes a correctness/availability defect to address before relying on
production availability. P2 denotes confirmed overload/supervision hardening;
the fault-injection and latency scope below must be preserved.

## AUD-001 — Revoke send authority before the first shutdown await

References: [close](../hummingbot/cli/service.py#L748),
[late revocation in stop_core](../hummingbot/cli/service.py#L433),
[runtime_healthy](../hummingbot/cli/service.py#L247),
[final REST authorization](../hummingbot/core/bot_ownership.py#L265).

`close()` sets the UI/runtime flags to stopping, then awaits funding cleanup and
reconciliation cancellation before `stop_core()` disables owner permission.
The final order gate checks the owner and `runtime_healthy()`, which deliberately
does not depend on the observer's running flag. Neither path checks the stop
request in this interval. SIGTERM sets the stop event without immediately closing
that permission. An already-queued connector create can advance while optional
cleanup yields, with a larger window if cleanup cancellation is slow.

The probe pauses funding cleanup, sets the stop event and invokes the real
`BotOwnership.authorize_rest()` using a valid synthetic tracked intent and mocked
DB/recorder checks. Observed: `phase=stopping`, `running=false`, owner permission
true, runtime gate healthy, final send authorized, and zero calls to `stop_core`.
**No transport was executed**; this proves an authorization window, not a measured
exchange order placed after SIGTERM.

Fix direction: close new-order and account-setup authority synchronously at
shutdown entry, before awaiting optional tasks. Retain cancellation/GET access,
owner fencing and uncertain-outcome recovery. Test SIGTERM and internal-error
cleanup while funding/reconciliation are slow and while a create/account mutation
is queued. A UI phase flag alone is insufficient.

## AUD-002 — Optional statement failure is treated as critical DB loss

References: [nested daily writer and exception filter](../hummingbot/model/bot_state_store.py#L129),
[publication call](../hummingbot/cli/service.py#L652),
[unhandled owner-loop exception](../hummingbot/cli/service.py#L737),
[process exit boundary](../hummingbot/cli/service.py#L811).

The optional daily writer correctly uses a savepoint, but its fallback catches
validation/data/integrity errors only. PostgreSQL lock timeout is an
`OperationalError` too, although the connection and owner session remain valid.
A short competing transaction holding a `daily_balance_snapshots` row makes the
upsert hit `lock_timeout=1000`; the error escapes `publish()` and `observe_once()`.
`run_service()` classifies the SQLAlchemy exception as critical persistence
failure and exits. Shutdown publication can encounter the same optional writer.

A real row lock reproduced SQLSTATE **55P03** in 1.027 seconds.
The same owner connection passed `check_owner()` afterward. No optional fallback
was published. This is a verified publication escape plus the inspected service
exit path; the probe did not boot a whole trading service or kill a cloud process.
The same exception-class gap exists for optional guard-history timeout handling.

Fix direction: distinguish narrowly recognized statement/lock cancellation from
connection/commit loss inside optional savepoints. Roll back only the optional
write, preserve a truthful heartbeat/control receipt, publish an unavailable
observation and back off. Keep real connection loss, recorder failures and failed
mandatory commits fatal. Add actual row-lock and statement-timeout cases rather
than mocking every `OperationalError` as a broken connection.

## AUD-003 — Server query timeout does not bound a missing response

References: [connection settings](../hummingbot/model/db_migration/postgres/connection.py#L18),
[synchronous owner query](../hummingbot/core/bot_ownership.py#L110),
[owner loop](../hummingbot/cli/service.py#L741),
[health on the same event loop](../hummingbot/cli/service.py#L292).

`connect_timeout` covers connection establishment, `pool_timeout` covers pool
checkout, and `statement_timeout` is enforced by PostgreSQL. None establishes a
demonstrated client response deadline for an already-connected synchronous
psycopg2 call. Owner checks, publication and recorder operations run on the same
thread as aiohttp. If the reply is lost, that thread cannot serve health or Stop
and the normal signal-driven shutdown timer has not yet been armed.

A local TCP proxy completed connection/authentication and an initial statement,
then suppressed server-to-client responses. After **four seconds** the client
still waited although PostgreSQL had canceled the query at two seconds and was
`idle in transaction (aborted) / ClientRead`. The child was explicitly killed by
the probe. This establishes that the two-second server limit does not bound the
client call; it does not measure an eventual OS/network timeout or a cloud restart.

Fix direction: establish and test a bounded client-I/O/fenced recovery strategy
that also leaves health/control responsive. Do not simply put a blocking call
inside `asyncio.wait_for`, or assume canceling `to_thread` stops the DB operation.
A dedicated owner connection must not be used concurrently after an abandoned
query. Test response loss as well as clean DB disconnect and ordinary row locks;
transport keepalive alone cannot detect every application-level response loss.

## AUD-004 — Reporting and Stop share an unbounded FIFO executor queue

References: [two-worker executor and submission](../hummingbot/cli/admin_api.py#L131),
[commands use the same executor](../hummingbot/cli/admin_api.py#L302).

The recent cap protects the SQL pool from API-only exhaustion, but it does not
prioritize control or limit pending work. Seven authenticated read requests each
run `pg_sleep(1.8)`, below the two-second statement budget, then an eighth request
submits Stop. With two workers, Stop waits behind the queued reads. The owner can
still acquire another connection and execute `SELECT 1` immediately.

Observed **HTTP 202 after 5.428 seconds**, before actual
Stop application. All seven reads returned 200; total offered concurrency is
**eight**, matching the deployment template. This already exceeds the five-second
Stop/cancel budget in this adverse slow-read case. It is not a claim that the
accepted healthy burst currently violates its SLO, and this probe did not measure
an OOM or sustained queue-growth incident.

Fix direction: bounded admission/backpressure for optional reads and control
priority or reserved control execution within a reviewed total DB budget.
Preserve idempotency, control CAS and owner fencing. Test queued Stop, command
receipt reads and disconnected clients while reads are slow. Increasing pool size
alone does not remove this executor queue ordering.

## AUD-005 — A dead action consumer is invisible to runtime supervision

References: [task checks](../hummingbot/cli/service.py#L207),
[listener creation](../hummingbot/strategy/strategy_v2_base.py#L293),
[action consumption](../hummingbot/strategy/strategy_v2_base.py#L729).

`runtime_problem()` checks the clock, connector tasks and controller loops but
omits `listen_to_executor_actions_task`. In the probe, the real action-listener
coroutine is canceled while the synthetic clock/controller remain healthy. A
`StopExecutorAction` batch is then enqueued. The listener is done/canceled, no
batch executes, yet `runtime_problem()` returns null and the send-health gate
returns true. A controller's stop/pause request has no consumer in that state.

This is an injected critical-task failure, not an observed spontaneous
cancellation or proof of a real exchange quote remaining live. The gap can leave
the process reporting healthy while quote creation/cancellation actions stop
being delivered. Existing done-controller-task tests do not cover this consumer.

Fix direction: supervise this required strategy task during active operation,
close admission on failure and use the existing position-preserving teardown.
Test both canceled/failed consumers and intentional shutdown to avoid false HALTs.

## Verification and follow-up

The final probe invocation reports **five passed / 243 deselected in 17.13 s**.
These tests assert the **defect-present behavior**: a passing probe confirms a
finding, not a Green runtime fix. All production files and the accepted image
remain unchanged. The two early fixture iterations corrected an incomplete Stop
fixture, snapshot freshness and proxy teardown; their raw logs are retained under
`build/availability-audit/` and are not product findings.

Portable JSON includes exact reproducer source and SHA-256 hashes. To replay from
a clean checkout, restore its `reproduction_sources` entries to their recorded
ignored `build/` paths, then run the recorded command. The existing disposable
runner validates a `hbot_test_` database prefix, removes its own temporary PG
container and does not read the private Demo environment. The shutdown test
calls authorization only; it never calls an exchange transport.

Recommended order: AUD-001 and AUD-002 first (narrow lifecycle/failure-isolation
changes), then AUD-005/AUD-004, and a deliberate bounded-I/O design/test for
AUD-003. Convert the probes to desired-behavior Red/Green regressions during fixes.
After runtime changes, bind new regression/image/load evidence to the new source;
do not reuse the accepted image's result as proof of the modified runtime.

Required safety exits must remain: lost ownership, recorder/mandatory persistence
failure and unresolved exchange effects should close submission and retain HALT
where required. The audit does not recommend continuing to trade through those
conditions, resetting recovery automatically or adding a new service/framework.
