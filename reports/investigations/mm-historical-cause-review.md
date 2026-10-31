# Historical timeout and latency review — 2026-11-01

Status: **bounded investigation complete; one diagnostic defect reproduced and
fixed. H1/H2/H3 do not have a proven historical root cause.**

This review uses retained artifacts and a short, isolated old/new watchdog
comparison. It does not rerun an hour gate, change trading behavior, deploy, or
operate the connected Demo account. [Portable evidence](mm-historical-cause-evidence.json)
records provenance, measurements and limits.

The accepted [full R1 and matched shipping R2](../release/mm-release-closeout.md) remain
image `5973410d` / manifest `50a95f3a`, measurement source `b7ba104e7`. Their
independently measured API latency and ledger results are unchanged. The new
diagnostic code is a separate follow-up, not a replacement source hash for those
completed runs.

## Confirmed diagnostic timestamp defect — fixed

The load watchdog captured a monotonic timestamp, then performed incident
trigger/summary I/O, then read CPU counters using that earlier timestamp.
Descheduling or slow evidence I/O could separate the timestamp from the actual
read by hundreds of milliseconds. It also reused that timestamp when evaluating
a newer heartbeat. This permits misleading CPU rates, negative heartbeat ages
and inaccurate alignment between CPU samples and probe failures.

In accepted R1 artifacts, estimated worst CPU timestamp skew is approximately
806ms for engine samples and 852ms for API samples. These estimates use nearby
UTC/monotonic mappings, whose own observed variation reaches about 32/16ms;
they are not exact scheduling measurements or corrected historical timestamps.

A packaged comparison executes the actual old and new watchdog functions on
the same accepted AMD64 image, with a real 20ms heartbeat, real Linux CPU counters
and an injected 800ms summary-I/O delay. It uses dummy interfaces and no network
or trading. The old CPU timestamp precedes completion of that delay by 804.05ms.
The new timestamp follows it by 0.410ms and exposes a 0.498ms counter-read interval.
The heartbeat remains consistent. This proves the instrumentation mechanism;
the injected delay does not prove what caused the original descheduling.

The correction is limited to test diagnostics:

- Timestamp counters immediately around their reads, retain both interval
  endpoints and label samples `sample_schema_version=2`.
- Read the heartbeat on both sides of its timestamp. Reject changing or future
  heartbeats rather than reporting a false valid stall; record stack/context
  collection separately from counter and heartbeat intervals.
- Sort analyzer samples across role streams and expose elapsed lower/upper
  bounds. Preserve counter deltas, but label old records `legacy_unbounded`.
  Overlapping reads with decreasing counters do not justify inventing a reset.

Red tests fail before the implementation. Five focused regression cases and
the existing diagnostic/export/control tests pass: **119 tests in 8.68s**.
The actual packaged old/new comparison independently confirms the timing fix.
No probe timeout, restart grace, load rate, latency budget, GC policy, pool or
cache setting changes. The runtime application and Docker image are unchanged.

## What the remaining incidents actually establish

| Incident | Established evidence | Limit / disposition |
| --- | --- | --- |
| H1: 498.033/s simulator burst against 500/s minimum | The original client hash matches revision `056b1149599b6455acdb927088f6b12631f833ca`. It already switches to burst mode before reading its baseline counters. | The failed inner 10s burst count/duration were discarded by an assertion; the 90s window average cannot reconstruct them. Mode-switch ordering is ruled out as the proposed defect. No reproducible generator cause; retain the failure. |
| H2:older sustained supervisor-liveness termination | The original daily fallback precedes termination by 126.698s; a successful engine cancellation callback precedes it by 9.877s. Original logs lack typed probe history and a final blocked stack. | Neither the SQL timeout nor the separately proven recorder GC deadlock is established as this incident's cause. No speculative engine fix. |
| H3:five failed probes over 7.206s | Logs retain engine fill processing at 08:32:51.126,54.324–54.889 and56.422–56.814 UTC, inside the failure/recovery interval. Generation-two engine GC retains only three completed events; none overlaps the incident and the largest is 244.99ms. | A 7.206s probe episode is not proof of a continuous 7.206s total process freeze. The API has a 660.52ms full collection ending 08:32:50.579, before the first failed engine probe at50.893; that event cannot explain the entire episode. Other GC generations, scheduling and synchronous work remain possible. No complete causal trace was enabled for this old run. |
| Old daily query cancellation | PostgreSQL itself logs `canceling statement due to statement timeout` at17:07:15.819 UTC. The matching API DB span lasts 2196.22ms with 14.58ms worker CPU. Thirteen HTTP200 responses explicitly report stale data. | This is a genuine server-side cancellation, not a client timestamp artifact. Without contemporaneous DB wait/IO/plan evidence, neither a specific slow operator nor shared-resource contention is proven. The separate 100,000-fill replay completes in 516.85ms and does not reproduce the cancellation. |

All timestamps above are UTC on 2026-10-31. H3 has `gc_diagnostics=true` but
`tracing=false`; the minimal full-GC list is not a full causal trace. Missing
checkpoint logs do not prove that no checkpoint occurred. A passing replay does
not explain a failed historical query.

## Latest passing runs: interpret recovered probes correctly

In R1's longest episode, the engine first fails at 19:19:00.440 UTC and recovers
at 19:19:05.027: three failed probes over 4.591s. Engine fill logs still occur at
00.675 and04.314–04.658 inside that interval. Retained stacks move between
executor reports, status publication, DB calls and normal transport handling;
a single snapshot frame does not attribute the whole interval to one function.
No engine GC event is retained within the surrounding 20s in incident/minute
evidence. Trace loss remains explicit, so this is not a universal exclusion of GC.

The supervisor probes engine and API sequentially, then waits before polling
again. Episode duration measures first failed observation to a later healthy
observation, including polling and other-role work. It does not measure a
continuous event-loop freeze. The same distinction applies to R2's longest
1.182s episode. Recovered misses are real; independent API/ledger gates still
pass and no healthy-window replacement/HALT occurs. The CPU timestamp defect
prevents precise historical CPU attribution; it does not erase these misses.

## Next action is evidence on recurrence, not a guessed runtime rewrite

There is no justified new SQL/cache/pool/watchdog change from this review.
Keep the existing ownership, position reconciliation and HALT protections.
H1/H2/H3 remain recorded as **insufficient historical evidence**, separately
from completed release acceptance.

If a similar incident recurs, use the corrected bounded sampler together with
the existing typed supervisor transitions, automatic prehistory/stack capture,
request/DB identifiers and explicit trace-loss counters. Preserve the exact
failed burst's control timestamps/counter deltas. For a DB cancellation, retain
concurrent backend wait/lock/IO evidence before assigning a SQL fix. A rolling
maximum, a coincident sampled stack, or a later passing run is insufficient to
claim a historical root cause. No additional service or unbounded telemetry is
introduced.
