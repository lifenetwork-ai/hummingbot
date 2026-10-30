# Engine liveness investigation — 2026-10-30

The retained evidence does not identify the root cause of the original long-lag
episode or the later engine restart. It does narrow the diagnosis and expose an
observability gap, addressed with tested supervisor diagnostics. No trading rule,
SQL ownership fence, probe timeout or restart grace has been relaxed.

## What the original restart means

The shipping supervisor probes each child on loopback with a 300ms HTTP timeout.
After a child has passed startup, failures spanning at least 15 seconds trigger
termination and the existing bounded restart/recovery path. An engine `/live`
response also returns HTTP503 if the observer has not progressed for over five
seconds, unless a deliberate bounded wait is registered. A stopped trading
session itself is not a liveness failure.

Previously, both a transport timeout and HTTP503 became the same `False` value.
The `child_unresponsive` event did not retain the reason, the number of failed
probes or supervisor sampling gaps. It therefore cannot establish a process
deadlock, an HTTP timeout, or a stalled observer by itself.

The first short wait-only attempt retained this timeline (UTC):

| Event | Time |
|---|---|
| Daily query timeout produces labelled stale fallback | 11:25:57.832 |
| End of measured 120-second load window | 11:27:42.405 |
| Engine logs a successful cancellation callback | 11:27:54.653 |
| Supervisor emits `child_unresponsive`, engine role | 11:28:04.530 |

The warning is 126.698 seconds after the daily fallback, 22.125 seconds after the
load window, and 9.877 seconds after engine activity. The engine was executing
callbacks during part of the interval leading up to termination. This is not
evidence of an uninterrupted 15-second total process freeze, nor evidence that
the two-second reporting statement directly caused the restart. The logs end
near test cleanup; the missing probe details cannot be recovered retroactively.

## What the 8.21-second measurement means

The first full soak's engine rolling maximum jumps to 8,210.78ms between samples
at 09:06:54.314 and 09:07:57.161 UTC. API rolling maximum reaches 2,907.69ms in
the adjacent sample interval. The test metric is lateness relative to a scheduled
50ms sample, not a timestamped stack trace proving one function blocked for the
whole 8.21 seconds. Missed sampling slots can accumulate during sustained work.

No generation-two engine GC completion is retained in that sampling interval.
This does not exclude other GC generations, CPU scheduling, serial synchronous
work or host/VM delays. The run used an AMD64 image under emulation on an ARM64
Mac. Resource contention remains a hypothesis, not an established cause.

The later traced probe samples the engine inside synchronous ownership checks,
status publication and recorder/order-intent PostgreSQL calls. These calls are
on the engine event loop; the API's separate process and pool do not isolate it
from shared database/CPU delays. However, a sampled stack is not a duration
attribution: at the worst retained heartbeat sample (1,150ms), the active DB ping
had started less than one millisecond earlier. It cannot explain the entire
preceding lag by itself. The trace also dropped 149,654 spans, so it does not
support a complete causal reconstruction.

## Change and tests

Supervisor diagnostics now distinguish `timeout`, `http_status` and
`connection_error`, retain the latest HTTP status and probe duration, count
consecutive failures, and report elapsed failure time and the largest probe
sampling gap. Logs occur on failure/recovery transitions and final termination;
normal startup misses stay silent. State remains bounded to two child roles.
No response bodies, URLs, headers or exception messages are logged.

Red/Green tests cover failure/recovery, exact existing restart deadlines, a long
supervisor scheduling gap, startup grace, child replacement, transport errors and
JSON logging. A real loopback HTTP server verifies that HTTP503 and a socket
timeout are distinct. A new close-error edge case in the diagnostic implementation
was caught and corrected before completion; this was not a bug discovered in e526.

The short diagnostic uses e526 **with the two supervision source modules mounted
over it**, with hashes recorded in its result. It is not an unmodified-image
release test. The first wait run passes at
`build/mm-reporting-isolation/20261030-192047-893218/result.json`: 120 exact fills,
same engine PID/owner/session, no HALT, and a durable fill observed during the
reporting timeout. It records six transient engine timeout episodes across the
fixture lifecycle, all recovered without restart. These episodes show probe misses under this workload;
they do not reproduce the earlier sustained-failure episode. The subsequent CPU
case at `build/mm-reporting-isolation/20261030-192822-eadbc2/result.json` reaches
the final feed-rate assertion after ledger/recovery/Stop checks, then **fails**:
simulator send rate is 99.93658/s against 99.96667/s. This metric counts sent
simulator frames, not engine-consumed updates, and does not by itself locate the
bottleneck. API p99 is 585.37ms, 120 fills are observed, a durable fill is witnessed
during the timeout, and four short engine timeout episodes recover with no
`child_unresponsive`. The failed rate gate is retained, not reclassified as a pass.

An earlier CPU attempt stopped at its startup metrics assertion before fault
injection (`20261030-192545-d84896`). Inspection found the diagnostic waited for
any metrics file, which can contain a previous startup snapshot. A regression now
requires a running observation before injection. The original failed result did
not retain that snapshot, so its exact contents cannot be proven retrospectively.

Final targeted native regression: **107 passed**, including real HTTP behavior,
supervision, service availability, logging and the startup metrics regression.
One prior run, concurrent with the Docker diagnostic, timed out in an existing
test's five-second initialization wait (106 passed, one failed). The unchanged
sequential repeat passes; the failure remains retained and is not attributed to
host contention as a proven cause. No startup or load budget was increased.

## What remains open

- The cause of the original long-lag episode and sustained probe failure is still
  unproven. No engine availability fix is claimed from this investigation.
- The existing full release and matched ordinary-entrypoint gates remain open.
  These diagnostics do not replace an unchanged passing release run.
- The diagnostic change requires a new packaged image before production use.
  Persistent Demo continues using e526; no live trading or deployment was performed.

If a sustained failure recurs, the new reason/status/count/gap distinguishes
HTTP failure from probe starvation before deciding a runtime change. Moving
ownership/order-intent DB calls into threads is not a safe blanket fix: their
session affinity and ordering relative to exchange mutations must be preserved.
Do not remove durable guards or increase watchdog budgets based on an unproven
cause.

See [machine-readable evidence](mm-engine-liveness-investigation-evidence.json)
and [full release report](e526-release.md).
