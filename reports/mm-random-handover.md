# Random handover and load-producer investigation — 2026-10-31

Status: producer defect fixed with Red/Green tests; two short diagnostics passed;
full-load confirmation on `dd602fb2` failed on a separately reproduced controller
scheduling race. Its correction passes shipping-image fault verification. The
next repeat exposes an OKX lost-order retention defect; all three corrections
are now packaged as `81fa1448` (source `fc0ee55b8`). Native release regression:
**2,092 tests and 11 subtests passed**; packaged E2E and supervised fault tests
pass. The 60-minute soak passes, but the full run fails its burst generator rate;
a separate same-image, same-fixture burst retry passes. See details below.
No production risk guard, health grace or acceptance deadline was relaxed.

## Confirmed defect

The load client continued generating new fills after its measurement window,
until it reached the nominal fill quota. At a Random handover an empty book
therefore became `fill_generator: backlog_not_drained` even with no request in
flight. A pending working-order lookup could also initiate a new fill after
generation had ended. The retained prior failure remains a failure:
`build/mm-load/20261030-215951-3d41e4` (3,598 soak fills, 10.153-second drain).

The corrected client closes generation at window end, rechecks that boundary
after the working-order lookup, and awaits already-dispatched fill requests.
It explicitly reports `fill_target_shortfall` and its drain policy. A stuck
request still fails under the original ten-second deadline. Original fixed-run
rate, HTTP error, latency, memory and ledger budgets are unchanged. Random-soak
rates were already declared observational because step admission can pause
quotes; that existing distinction is no longer contradicted by post-window
synthetic fill creation. No missing fills are invented or silently discounted.

Two new regressions failed on the old client, while dispatched/stuck request
controls passed. All four pass after the change; the empty-book case also
checks that zero throughput fails the fixed-run rate gate (including its
existing one-fill tolerance). Related budget/exchange regression: 31 passed.
Expanded quote-guard/volatility characterization: 107 passed. Raw evidence is
under `build/random-handover-20261031`.

## Runtime observation

The opt-in `scripts/test_mm_random_handover.py` reuses the full-load image,
100,000-row fixture, 2-CPU bot, pool/TTL and guards. Only Random step duration
is shortened to 30 seconds for a 180-second diagnostic. Bounded wrappers record
the original guard/refresh decisions without changing returned actions.

First run `build/mm-random-handover/20261031-043633-11a09f` passes: 174 actual
fills, all matching recorder quantities/fees and positions; six-fill shortfall
reported, zero errors, drain 0.029s. Its records include a perpetual cancellation
with `inconsistent_own_depth`: the acknowledged private-order state and public
book do not yet agree. Existing guard tests require waiting in that case and
qualifying fresh public observations before resuming. A new sequence test
confirms that this wait recovers, without bypassing the protection or latching
a permanent HALT. This identifies an observed safety wait, not an OKX network
incident: all exchange traffic is local simulation.

Confirmation `build/mm-random-handover/20261031-044129-a1fcc9` also passes with
174 matching fills, six-fill shortfall, zero errors, 0.102s drain and primary
p99 102.55ms. Its added first-refresh records verify that the observation hook
executes even when no quote fails validation. Each short run includes the initial
fixed spot primer plus seven Random step-start events; it is not an hour-long
load acceptance. The cancellation observed in the first run did not require a
runtime patch; original safety behavior remains intact.

The short run does not prove the exact historical inputs at every cancellation
in the older soak; its probe records must not be retroactively assigned to that
older event. The older sustained-liveness incident is also a separate unresolved
cause, not closed by fixing this client.

## Confirmation

The repeat used the same full warm300/mixed900/burst90/soak3600 and fault
configuration with the corrected client, without diagnostic quote wrappers.
Its HALT prevents completion of the post-soak running-state check and later
fault cases; it is retained as failed, not merged with earlier passing runs.

Run: `build/mm-load/20261031-044542-876453`. Image identity matches the prior
failed run; comparing fixture hashes shows only `load_client.py` changed, and
all recorded harness-source hashes match. Warmup passes with p99 230.00ms,
300 matching fills and positions. Mixed and burst also pass. The 60-minute
soak fails: at 49m25s the engine persists `controller_task_stalled` HALT.
The client records 71 unexpected/degraded responses, p99 2446.14ms and only
2,952 soak fills. Its 648-fill shortfall is caused by halted trading, not a
benign Random gap. Drain is 0.187s, confirming the earlier producer fix does
not wait for nonexistent future fills. All 4,242 actual fills across the four
windows match the recorder quantities/fees; retained data is under
`build/random-handover-20261031/failed-soak-*`. Native follow-up tests were run
after the incident while the failed soak finished, not before its onset.


## Controller watchdog defect

`ServiceHost.observe_once()` previously persisted HALT immediately when a
controller heartbeat was over five seconds old. After an event-loop scheduling
delay, the observer may run before an expired timer wakes that healthy controller.
That ordering produces a permanent recovery requirement even though the task
can immediately make progress. The synchronous order-send gate correctly treats
stale progress as unsafe; the defect is promoting that first sample directly to
a persistent task-failure decision.

Red/Green tests cover a queued wake callback, an expired timer, a genuinely hung
controller, an explicit task failure during confirmation and cancellation. The
first two failing scenarios identify the race; the hung-task control still halts.
An intermediate two-`sleep(0)` approach failed the added expired-timer case and
was replaced before packaging. The final implementation awaits a zero-delay
**timer checkpoint**, then repeats the original health check. It never rewrites
a heartbeat, raises the five-second deadline or bypasses the send gate. An
actual task failure still latches HALT; fresh progress can recover before that
durable decision. Diagnostic logs retain controller ID, stage and age.
Affected service/controller regression: **37 passed**.

A shipping-image experiment makes the engine temporarily unscheduled using
SIGSTOP/SIGCONT, below the existing supervisor grace. The old `dd602fb2` image
fails on its first 6.529-second pause (`build/controller-stall/20261031-061001-f3f392`):
same engine PID, probe recovers, but `controller_task_stalled` HALT persists.
This establishes a causal scheduling/HALT defect independently of the host's
unknown reason for the original delay. A preliminary script attempt failed
because the minimal image lacks an external `kill` command; that harness failure
is retained separately and the repeat uses Python `os.kill`.

New immutable image `dbd5b5ba` (source `d4dda9f3b`) passes the identical shipping
experiment: three 6.523/6.533/6.552s pauses, unchanged engine PID/session, no HALT,
and eight exact, duplicate-safe recorder fills with matching positions. Logs
show both controllers were in `sleep`, 6.5–6.9s overdue, then made real progress
before the confirmed check; the patch did not invent a healthy heartbeat.
Artifact: `build/controller-stall/20261031-061636-c97288/result.json`. Packaged
`service.py` hash matches the reviewed source. Its subsequent full-load attempt
failed the mixed fill-rate gate and was interrupted during partial soak. Native
regression passes **2,069 tests and 11 subtests**; all **17 packaged E2E groups**
and shipping supervision pass. The final image results are recorded below.
The separate older sustained supervisor-liveness incident still lacks enough
evidence to identify its historical cause; this report does not claim otherwise.


## Confirmed OKX lost-order retention defect

The next load (`20261031-065903-043941`, image `dbd5b5ba`) passes warmup
and burst, but fails the unchanged mixed fixed-profile fill-rate gate: 897/900
fills. Mixed API errors are zero, p99 is 117.61ms and drain is 0.064s. All 1,197
fills through mixed reconcile. A perpetual quote refresh removes the next
required BUY cohort near the deadline. This is not a passing release gate.
The subsequent partial soak was intentionally interrupted after identifying a
separate connector defect; it must not be counted as a completed soak.

A startup order blocked before placement remains in the connector's lost-order
tracking. The exchange replies HTTP 200 with `code=51603, data=[]`, but both
OKX connectors index `data[0]`, raising `IndexError`. Their status-error
classifiers were unimplemented, and upstream tests for this case were explicitly
disabled. The absent order is repeatedly polled (269 warnings in the retained
log); accumulated retained orders can also affect own-order guard accounting.
The [official OKX error table](https://www.okx.com/docs-v5/en/) identifies 51603
as “Order does not exist.” Transport success is not order-status success.

The fix decodes this response into a typed absence error and reuses Hummingbot's
existing lost-order lifecycle. It does not delete recorder history or invent a
second tracker. Malformed data, network timeouts, rate limits and other exchange
errors are not treated as authoritative absence. Existing clock-error handling
is preserved. Cancellation-error handling is outside this targeted change.

TDD: both previously disabled upstream lifecycle tests fail before the fix;
after correction, the affected suite passes **126 tests and two subtests**,
including 23 new response-classification checks. The shared checks are included
in `make test-cloudrun-regression`. The final image, 60-minute soak and targeted
burst repeat are verified below; the combined full run remains failed. This
defect has **not** been established as the cause of the three-fill mixed-window
shortfall, and the report makes no such claim.


## Why earlier tests did not catch these defects

| Defect | Missing coverage | Added regression |
| --- | --- | --- |
| Post-window fill generation | Empty book / pending lookup at generation deadline were not distinguished from dispatched work | Boundary tests fail before the correction; dispatched and stuck-request controls preserve drain semantics |
| Controller false HALT | A delayed healthy controller and observer could be scheduled in the opposite order; hung-task coverage alone did not exercise it | Ready callback and expired timer Red/Green tests, plus old/new shipping-image SIGSTOP/SIGCONT reproduction |
| OKX lost-order retention | Both status-not-found classifiers were TODOs and inherited lifecycle tests had been overridden with disabled test bodies | Re-enable upstream lifecycle tests; classify explicit absence separately from ambiguous payloads and transport failures |

A high passing test count did not cover these missing branches. The prior review
missed disabled upstream coverage and asynchronous boundary conditions. The
new regressions constrain the demonstrated defects; they do not establish that
all scheduling, exchange or retention failures are impossible.


## Final image validation and remaining limitation

Immutable image `sha256:81fa144825b1c63aef634e71a19b7e371ede33c430f607c3d84ec649df7c3fb6`
packages source commit `fc0ee55b8`; SHA-256 checks of the changed connector
files and service match source. Native release: **2,092 tests, 11 subtests**;
PostgreSQL integration: **268 tests**; packaged E2E: **17 groups**, including
upgrade/rollback; shipping supervision: passed. Controller scheduling fault
experiment on this image: three 6.55–6.58-second engine pauses, same PID/session,
no erroneous HALT, eight unique fills and positions reconcile.

Full unchanged load run `build/mm-load/20261031-082445-9a99bd` remains **failed**:

| Window | Result | Actual evidence |
| --- | --- | --- |
| Warmup 300s | pass | 300 fills; zero errors; API p99 346.58ms |
| Mixed 900s | pass | 899/900 fills, within existing one-fill tolerance; zero errors; p99 272.49ms |
| Burst 90s | **fail** | generator measured 498.033/s, below required 500/s; API p99 911.01ms; 90 fills |
| Random soak 3600s | pass | 3,594 fills, shortfall 6 observed; zero errors; p99 138.24ms; drain 0.179s |

The burst failure is a failed feed-rate assertion, not an HTTP failure: all
1,800 primary and 450 auxiliary requests returned 200. The exact reason the
simulator missed the target in that run is not established. All six injected
fault scenarios passed. All **4,883 actual cumulative fills** through soak match
recorder quantities/fees; positions agree. Random soak throughput is observed,
as declared by the existing gate, because readiness can pause quote generation.
No claim is made that each of the six ungenerated fills is caused by a specific
transition.

There were 58 minute samples: 56 `running`, two `waiting_for_mm_readiness`, no
HALT, no PID change. At sample 21, volatility defense logged perpetual
`market_liquidity_unqualified` at 02:09:18.811 UTC and `normal` at 02:09:19.817;
at sample 33 the same occurred for spot at 02:21:42.694 and 02:21:43.702 UTC.
These are observed brief safety waits, not engine restarts. Supervisor logged
19 transient probe failures and all 19 recoveries across measured windows.
The largest sampled engine event-loop delay was 1.425s; this run cannot establish
absolute isolation or identify the original unexplained sustained-liveness event.

The 58-sample retention gate passes: first/last-five median RSS growth is 0 MiB
supervisor, 13.99 MiB engine, and 0.125 MiB API; engine threshold is 64 MiB.
Thread and FD checks also pass. The harness label `RSS` in its summary prints
container cgroup memory including page cache, while process RSS is measured
separately; these values must not be added as independent physical memory.
One hour without excessive growth is not proof that indefinite execution can
never leak.

Independent burst retry `build/mm-load/20261031-095522-67306b` on the same
image, with matching source/fixture hashes and unchanged 500/s minimum, **passes**:
509.80 delivered updates/s for 10.004s; zero errors, API p99 562.68ms; 90 unique
fills and positions reconcile. It is a separate retry with a fresh fixture, not
a retroactive pass for the failed combined run. A single pass does not prove
why the prior generator achieved only 498.033/s or eliminate recurrence.

Demo preservation check: image and stopped control/ack versions unchanged; all
orders, trade fills, executors and positions hashes match the baseline. Local
test containers were removed. No Demo or live exchange orders were submitted.
