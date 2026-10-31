# Random handover and load-producer investigation — 2026-10-31

Status: producer defect fixed with Red/Green tests; two short diagnostics passed;
full-load confirmation on `dd602fb2` failed on a separately reproduced controller
scheduling race. Runtime correction `3789b2500` is being packaged for verification.
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
`service.py` hash matches the reviewed source. Full release confirmation is
still pending. Full native release regression passes **2,069 tests and 11
subtests**; all **17 packaged E2E groups** and shipping supervision pass.
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
in `make test-cloudrun-regression`. Packaging and full load verification remain
pending. This defect has **not** been established as the cause of the three-fill
mixed-window shortfall, and the report makes no such claim.
