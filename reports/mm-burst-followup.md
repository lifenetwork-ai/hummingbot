# Remaining acceptance investigation — 2026-10-31

Later follow-up: [automatic incident capture and sequential shipping comparison](mm-incident-investigation.md)
is complete. Both new diagnostic sequences pass; the historical long-lag cause
and combined release acceptance remain open. Results below retain their original
scope; the planned passing-baseline acceptance control is still not closed.

Status: bounded diagnostics complete; an additional load-client head-of-line
bug is fixed and verified. The earlier three bugs stay closed. Current-candidate
release acceptance remains **open**: its burst latency gate fails, and a matched
warm-state traced diagnostic does not reproduce that episode. No speculative
trading runtime change is made.

## Starting evidence

Combined run `20261031-082445-9a99bd` failed burst emission at 498.033/s against
the existing 500/s minimum. Its warm/mixed/60-minute soak and six recovery faults
passed. Separate burst retry `20261031-095522-67306b` passed at 509.80/s. Image,
source and fixture hashes matched, but workload state differed: the retained
exchange ledgers show 319 orders created before the combined burst's first
control, versus 17 before the fresh retry's control. The final states of those
orders cannot all be assigned retroactively to that timestamp.

The latest image `81fa1448` is absent from the current Docker daemon. The unchanged application source
is rebuilt below before packaged diagnostics; no persistent Demo image or
database is replaced.

## Tested diagnostic correction

The burst assertion previously raised before its result was added to the output.
Failed windows therefore omitted exact frame count/duration/control metadata.
Five new tests fail before the diagnostic change; 36 related tests pass after it.
The existing feed threshold, counting tolerance and restore-to-steady behavior
are unchanged.

Opt-in simulator diagnostics retain at most 64 observed seconds: actual delivered
and failed frames, scheduling lateness, book construction and socket-send maxima,
active subscription counts, process CPU and cgroup CPU/throttling counters.
Counters increment only after actual successful sends. Native tests also cover
closed transports and verify snapshots cannot mutate retained metrics. Ordinary
load does not enable the collector. This is test instrumentation, not a deployed
admin endpoint or a new production monitoring service.

Raw Red/Green logs: `build/burst-investigation-20261031/`.

## Pending

- Short producer diagnostics and bounded historical review: completed below;
  historical causes remain unproven.
- Current candidate combined release gate: attempted, fails burst latency.
- Ordinary-entrypoint control: not run because its baseline has not passed.

No historical failed result is changed or merged with a separate passing retry.


## Bounded short results

Native tests pass **36 cases** after the five initial Red failures. Diagnostic
commit: `44c82ecd8`. Restored image `5973410d` has the same verified service and
connector hashes as `81fa1448`; tracked application source, controller, build and
configuration files have no diff from the previously verified runtime commit.
Runtime ABI/JIT/TLS/logging smoke passes. The image was rebuilt because the old
artifact was no longer present, not to apply a speculative engine fix.

| Short case | Feed/s | API p99 | Result |
| --- | --- | --- | --- |
| Diagnostics disabled | 509.67 | 1,476.59ms | **fail**, primary and auxiliary p99; zero HTTP errors, 90 matching fills |
| Diagnostics enabled | 509.83 | 97.47ms | pass; zero errors, 90 matching fills |
| Diagnostics plus 307 known canceled order snapshots | 509.78 | 75.64ms | pass; zero errors, 90 matching fills |

All three use the same image, resource caps and threshold. The third changes
only the explicitly recorded diagnostic exchange fixture to preload canceled
history under non-bot identities. It does not replay prior positions/fills or
reconstruct the original subscription timing, and is not release acceptance.
The ordinary and instrumented fresh cases share source/fixture hashes. These
results are sequential observations, not a statistically established causal
comparison or permission to discard the first failed p99 result.

Both instrumented bursts show four book subscriptions and no failed sends or
cgroup CPU throttling. Their ten-second process/container CPU demand is modest:
about 0.80s fresh and 1.13s with terminal history. Maximum scheduling lateness is
21.83/62.58ms; maximum wall time building one book is 0.56/52.82ms and sending is
0.50/15.50ms. Wall spans can include descheduling; they do not establish CPU
attribution to the book algorithm. Both delivered the required rate, so the
307-order preload did not reproduce the shortfall. The reason for the original
498.033/s measurement remains unproven; the newly retained measurements are
available if it recurs. The bounded short investigation stops here rather than
repeating until every failed metric disappears.

The first short case has API handler maxima around 471ms, API worker queue
maximum around 63ms, and end-to-end p99 1.48s. The client metric deliberately
includes scheduled dispatch/backlog time. Existing aggregate metrics do not
attribute every slow request to a complete timestamped path. No SQL, client or
engine root cause is assigned without that evidence.

## Focused supervisor review

The original `child_unresponsive` occurs 126.698s after the reporting fallback,
22.125s after the measured load window and 9.877s after a successful engine
cancellation callback. The original event lacks typed probe status, consecutive
failure timestamps and supervisor scheduling gaps. Its eight-second rolling
lag maximum is not a timestamped stack-duration attribution, and the old trace
also drops spans. These missing historical facts cannot be recovered by a fresh
passing test. Outcome: **not reproduced / insufficient historical evidence**.

Current supervision retains reason/status/count/elapsed/gap and controller
progress details. Existing shipping fault tests cover API/engine crash and hang,
ownership/recovery and retained HALT. Recheck that harness on the restored image;
do not expand watchdog grace or bypass ownership to make an unknown event pass.
The original cause stays open. The closed controller scheduling race is not
retroactively assigned as its cause.

Machine-readable measurements: [follow-up evidence](mm-burst-followup-evidence.json).

Shipping supervision on restored image `5973410d` passes all existing cases,
including independent API replacement, engine crash/hang, retained HALT and
parent cleanup. This verifies current behavior; it does not identify the old
incident cause. At that checkpoint the full gate was the next separate acceptance step; its
later failed result is recorded below.


## Additional confirmed load-client defect

The first restored-image full attempt `20261031-145220-cb1675` fails warmup
fill rate (296/300), with zero HTTP errors, p99 74.91ms and drain 0.011s. It is
interrupted during partial mixed after a separate, reproducible test-client bug
is confirmed; no completed full-load or retention acceptance is claimed.

Boundary logs show spot SELL quotes canceled 7.6s before the warmup deadline,
with replacement 0.45s before it. Other families continue filling. The generator
waits for its exact next family instead of using another executable quote. A
new two-second real-client regression offers a continuously working spot BUY:
the old client accepts only one fill while waiting for a nonexistent perpetual
BUY. It fails Red; the corrected client accepts two. Expanded related suite:
**40 passed**, including all previous generation/drain boundary cases.

The correction rotates across available executable market/side families and
returns to a missing family once available. It also skips an exhausted first
order if another level has capacity. Only real live/partial orders with enough
remaining size can be chosen. The output records actual fills by market/side.
This is an explicit workload-selection correction: unavailable quotes no longer
block other available families. The existing minimum total fill rate, window,
partial-fill amounts, fee injection, duplicate frequency, order-state checks and
ten-second drain deadline are unchanged; no new fills are generated after the
measurement deadline. All four families must remain visible in the subsequent
load evidence. This is not evidence that the bot lost fills, nor the root cause
of the unrelated 498.033/s feed shortfall. Targeted packaged confirmation follows.


Targeted packaged confirmation `20261031-150311-b31ae8` passes for 300 seconds:
300/300 fills, zero errors, API p99 512.18ms, matching fills/fees/positions. Actual
family counts are spot BUY 78, perpetual BUY 74, spot SELL 76, perpetual SELL 72.
This verifies coverage and executable-quote selection on the corrected harness;
it is a short diagnostic, not the full release gate. Fix commit `48d2967d0`.
The corrected-harness full attempt below fails burst latency; the interrupted
initial attempt and all short results remain retained.


## Corrected-harness release attempt: latency still open

Run `20261031-151026-01897f` uses the same restored image, unchanged load budgets
and the corrected selector. Warmup passes with 300 fills/p99 88.98ms; mixed
passes with 900 fills/p99 360.01ms. All four families execute in both windows.
Burst delivers 509.80 updates/s, 90 fills, zero HTTP errors and matching positions,
but **fails primary and auxiliary p95/p99**: primary p95 3,029.52ms and p99
4,622.38ms. All 1,290 cumulative actual fills through burst match the recorder.
This failed attempt is interrupted during partial soak for focused diagnosis;
there is no completed new 60-minute or six-fault acceptance to claim.

At burst offsets 70.689–77.889s, the supervisor observes five engine timeout
probes, recovers after 7.206s, and does not replace the engine. The feed burst
had ended at 52.974s, about 18s before that episode. Maximum sampled engine
scheduled lateness reaches 6,722.72ms, API 1,337.63ms. The lateness metric alone
does not establish a continuous total freeze or the function responsible.
API worker-queue maximum is 1,991.55ms and handler maximum 2,501.69ms; therefore
the earlier hypothesis of only a delayed client is not sufficient.

Across retained bot-cgroup observations at offsets 66.850–77.504s, throttled
periods increase by 29 and throttled time by 1.579s. This is shared application
quota pressure, separate from the simulator's zero-throttle feed measurement.
It overlaps the slowdown but cannot explain the entire lag by itself. Timing
and CPU attribution to an individual operation are missing at this event.
No engine/SQL/pool/priority change is justified solely from these aggregates.

## One bounded warm-state trace, not another full-release retry

A fresh traced burst `20261031-153850-267589` passes at p99 136.82ms and does not
reproduce the late episode. A closer diagnostic repeats warm300/mixed900/burst90
with existing bounded trace, then intentionally stops before soak:
`20261031-154316-871c04`. Source/fixture hashes match the failed full attempt;
trace activation and the explicitly recorded diagnostic wrapper are differences.
It passes all three windows: 300/900/90 fills, p99 272.64/213.55/183.58ms and
matching quantities/fees/positions. Burst rate is 509.62/s. Its largest captured
burst loop stall is 290.05ms, so the 6.7-second event remains **not reproduced**.

The trace records two throttled periods/0.111s over the 90-second burst, unlike
the higher-pressure failed episode. This is an observation, not proof that
tracing, cache state or host scheduling is the cause. Selected slow-root traces
drop 144 spans, and the cumulative server trace counter also reports dropped
spans; one PostgreSQL sampler error exists elsewhere in the run. No complete
causal reconstruction is claimed from incomplete or non-reproducing traces.

The bounded investigation ends with the current latency/long-lag issue open.
The old simulator-rate and old sustained-supervisor causes also stay unproven.
These diagnostics do not close the combined release gate, and the planned
ordinary-entrypoint acceptance control is not started from a failed baseline.
The next useful evidence is timestamped operation/stack/CPU attribution during
this specific recovered-probe episode, including a comparison with shipping
entrypoint instrumentation if justified. Repeating hour gates until one passes
would not identify its cause.

Demo image, stopped control/ack versions and all four ledger fingerprints remain
unchanged. Temporary diagnostic containers are cleaned. No deploy or connected
Demo/live exchange action is part of this work.
