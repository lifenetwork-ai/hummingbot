# MM execution and independent venue-quote observations

Date: 2026-11-02 (Asia/Saigon). **Instrumentation and bounded verification are
complete; uninterrupted two-sided quote availability is not certified.**
[Evidence](mm-trading-continuity-evidence.json),
[plan](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#trading-continuity-diagnostics--requested-2026-11-02),
[commands and definitions](../../docs/life-mm-load-tests.md#mm-activity-and-independent-quote-observations).

Subsequent [quote-replacement investigation](mm-quote-recovery.md) confirms and
fixes an avoidable five-second shutdown wait after terminal confirmation;
matched short diagnostics improve the measured gaps. Protective pauses and a
production coverage SLO remain separate. Results below retain their original scope.

## What was added

The opt-in `--mm-activity` diagnostic reuses the existing load wrapper, incident
recorder, simulator and analyzer. It records strategy tick/controller-cycle
start gaps and durations, readiness, instance identity, and spot/perpetual
connector submit/cancel call-to-return timing. Returns, false returns, errors and
cancellation remain distinct; original results/exceptions are preserved.

An independent load-client task samples simulator live-order age every second,
with client request/response bounds and simulator monotonic timestamps. Filled
and canceled quotes leave the active tracker. Simulator cancel request-to-terminal
observations are separate from connector returns. This runs outside the engine,
so an engine pause cannot stop the observer. No production API/env setting, risk
decision, database migration or trading-rule change is introduced.

Memory/disk are bounded: weak runner keys, 256 recent engine events, 256 live
quote identities/terminal events, 16MiB client output and a 128-record queue,
plus existing incident-recorder limits. Capacity/I/O/metadata loss, invalid
clocks, sampling gaps and missing observations remain explicit. Per-episode
deduplication/caps and empty-sample grouping are tested. Old runs say
`not_instrumented`, rather than zero downtime.

## Verification and observed behavior

Final native CLI suite: **946 passed, seven reported dependency warnings**.
Red/Green covers the new measurements, weak-reference retention, sink/metadata
failures, exception/cancellation preservation, venue terminal routes, independent
clock bounds, cross-episode isolation and missing/empty sample interpretation.
Implementation is `5ce488ba7`; empty-sample analysis is `8bf24f5a6`.

The unpaused 180s diagnostic uses the all-feature/volatility-defense/operator-read
fixture and 100,000 historical orders plus 100,000 historical fills. Existing
load budgets pass: API p99 356.74ms, zero unexpected API errors and 180 independently
reconciled **synthetic** fills/fees/positions. These are simulator transactions,
not OKX Demo/live trades. The observer retains 180 samples, no sample/clock/sink
errors or file drops; its maximum sampling gap is 1.126s.

Four engine probe failures/recoveries are retained, longest observed transition
1.218s. In their timestamp contexts, strategy tick-start gaps are approximately
0.997–1.058s; controller-cycle gaps reach 2.007s. Thus these probe episodes do not
establish a matching complete pause of the strategy clock. The controller loop
sleeps after finishing work, so start gaps include work and its configured sleep.
Capture is bounded; these observations do not explain every older probe event.

Process-lifetime maxima through the latest export, **including preparation**:

| Measurement | Observed maximum |
| --- | ---: |
| Strategy tick-start gap | 1.361s |
| Controller-cycle start gap | 2.007s |
| Connector submit call-to-return | 412.06ms |
| Connector cancel call-to-return | 201.20ms |
| Resting age at a venue point sample | 61.51s |

Connector timings exclude work before method entry and include awaiting/scheduling;
they are not full decision-to-exchange or cancellation-terminal latency. A local
submission block remains an exception outcome, not an acknowledged venue order.
Resting age does not by itself prove stale pricing or a refresh-policy violation.

## Controlled pause validates the detector

A separate 90s diagnostic starts with 12 verified live quotes younger than 1s,
then sends SIGSTOP/SIGCONT only to the owned isolated engine. Linux confirms its
stopped state for 2.272s. Three independent samples inside that interval retain
12 live quotes; their maximum resting ages increase 0.948→1.950→2.953s. The strategy
tick-start gap reaches 2.657s and controller gap 3.164s. The engine PID is unchanged,
one corresponding probe episode recovers, and MM execution resumes.

This intentionally injected fault demonstrates detection, not a healthy release
gate or the cause of prior spontaneous probe episodes. It uses zero seeded
history, so its API timings must not be compared as an optimization against the
100k-history diagnostic.

Two preparation attempts are retained separately. A container selector initially
matched the client as well as the bot; it was interrupted and corrected before
injection. A subsequent pause landed while quotes were already absent: two
earlier samples were also empty, and the live-quote assertion failed. Its raw
90s driver passed existing load budgets, but that helper attempt is **not marked
passed**. The final case adds the fresh-live-quote precondition above. No failed
attempt is stitched into the successful case.

## Open operational acceptance: quote coverage

The unpaused diagnostic also observes **three groups of seven empty venue
samples**: approximately 60.2–66.2s,129.6–135.6s and159.7–165.7s from window start.
Each group spans about 6.02s between its first/last empty point. Canceled-order
batches precede the groups; replacement acceptances appear afterward. Discrete
samples do not prove continuous absence throughout or precise transition times.

The gate still passes because its existing API/fill/rate/ledger contracts do not
define uninterrupted resting-quote coverage. There is no established new runtime
defect or complete cause attribution here. **Before claiming continuous quoting,
correlate these clusters with existing guard/admission/executor decisions and
declare the acceptable replacement/guard gap.** Preserve safety stops; do not
disable guards simply to make quote counts positive. This was an explicit
follow-up at this checkpoint; the subsequent investigation linked above fixes
the executor wait while preserving the quote-policy limitation.

## Artifact and scope

The operator confirmed manually deleting prior bot images. After a premeasurement
lookup failure, a full Dockerfile rebuild produced diagnostic image `4ef2c50a`
(`hummingbot-cloudrun:mm-continuity`). Its 1,155 fingerprinted runtime/controller/config source files match
the former `95cde31c`; source label is `b5e589f86`, with instrumentation mounted
from `5ce488ba7` and postprocessing from `8bf24f5a6`. The fingerprint set covers hummingbot, controllers and deployment config;
maintenance scripts are outside that comparison. Matching source is not binary,
dependency or artifact equivalence. **This new image does not inherit the prior
full R1/R2/sixty-minute acceptance.** No new hour gate, connected Demo, service
upgrade, real trading or Cloud Run deployment is included.

Raw natural run: `build/mm-load/20261102-200451-3a58bc`.
Successful controlled run: `build/mm-load/20261102-201429-3fb00d`.
Red/Green, retained attempts, build, hash verification and analysis:
`build/mm-continuity-20261102/`. Owned containers/networks are removed; the new
diagnostic image, unrelated containers and original Demo volume/config are kept.
