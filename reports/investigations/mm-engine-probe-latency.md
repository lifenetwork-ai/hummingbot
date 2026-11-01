# Engine probe latency and diagnostic overhead

Date: 2026-11-02 (Asia/Saigon). Status: diagnostic improvements verified;
runtime probe-delay attribution remains open. Portable
[evidence](mm-engine-probe-latency-evidence.json) retains hashes and exact samples.

The existing evidence does not uniquely explain the recovered probe delays.
This follow-up improves the fidelity and cost of the existing offline recorder.
Trading logic, ownership checks, SQL deadlines, restart grace, GC policy and
production cache/pool settings are unchanged.

## Changes supported by tests

1. A busy trace previously retained its first N child spans. A slow ownership
   check appearing after many short calls could disappear. The same bounded
   capacity now retains the longest spans and returns them in completion order;
   parentage and omitted-span counts remain explicit.
2. Bulk CPU/context records could consume the entire incident file before a
   later probe transition. The existing 32 MiB per-role ceiling now reserves
   8 MiB for probe failure/recovery, loop-stall and GC records. Append/restart
   accounting preserves both budgets. Queue capacity remains 256 and history
   remains 256 records/512 KiB. Queue or reserved-budget exhaustion can still
   drop evidence; bounded per-kind loss counters expose those losses.
3. The hot `SpotOutflowProjection.advance` diagnostic wrapper now retains
   scalar wall/thread-CPU timings for every call but creates a detailed span
   only for a completed call taking at least 100ms. This avoids allocating
   trace identities/children for tens of thousands of very short calls.
   Enclosing spans and watchdog thread stacks still cover in-progress work;
   an in-progress leaf is not registered separately. The threshold is a test
   observation policy, not a timeout or a trading rule.
4. The existing wrapper separately times ownership checks, fill storage and
   market-state checkpoints. It records no method arguments or private state.
5. `analyze_mm_load_trace.py --incidents` correlates probe transitions with
   same-role retained spans, GC and loop stacks. It deduplicates cross-process
   copies, handles supervisor sequence resets and labels missing recovery.
   Transition durations are supervisor observations, not measured quote downtime.
   Context includes one second before the failure marker because a timeout is
   reported after the attempted probe. `intersects_transition` distinguishes
   preceding work from observations inside the transition interval.

## Red/Green and replay

Initial Red has 6 failures/13 existing passes for late-span retention/reserved
capacity. Analyzer Red has 2 missing-feature failures/3 passes, followed by a
separate publisher-restart regression (1 failure/5 passes). The slow-leaf wrapper
regression fails before implementation (1 failure/2 passes). Final selected
tracing, incidents, timing, worker-queue and analysis regressions: **61 pass**.
The extra pre-probe context case also has its own Red (1 failure/6 passes).
Flake8 and diff checks pass.

The native paired replay executes 50,000 tiny synchronous calls, in three
shuffled repeats per mode with the actual 100ms threshold and 16-child capacity.
Median calling-thread CPU:

| Diagnostic mode | CPU |
| --- | ---: |
| Previous first-N recorder | 555.60ms |
| Longest-span recorder, detailed tracing on every call | 590.59ms |
| Hot-leaf policy: scalar metrics on every call, detailed slow calls | 87.25ms |

The hot-leaf policy reduces this measured wrapper cost by about 84%. Longest-span
selection alone costs about 6% more in this replay; its benefit is retaining
useful late evidence. These are local diagnostic costs, not an 84% improvement
in trading throughput or proof that tracing caused all earlier delays. Thread
CPU excludes descheduling; host/build activity can still affect the experiment.
Full samples and the fixed shuffled order remain in raw evidence.

## Current verification scope

Docker cleanup between sessions removed the accepted bot image/container and
build cache. The `hummingbot-okx-demo-data` volume still exists and is untouched.
A clean Dockerfile rebuild creates diagnostic image `eb72604c` (1,368,902,212
bytes). Range-constrained dependencies were resolved again; it is not the same
artifact as accepted `a190b024`. Four application logging/guard module hashes
match the accepted source. Credential-free packaged smoke passes ABI/JIT/TA-Lib,
TLS, 63 connectors, 17 SQL scripts and structured severity/redaction checks.

The declared probe is 300s on an internal simulator network, with 2 CPU/2 GiB
application, 100,000 historical fills, original rates/latency budgets, all-feature
guards, operator reads, TTL 15 and incident cap 128. It is a diagnostic, not a
full R1/R2 release, connected Demo trading or a new hour-retention acceptance.
Run `20261102-035446-903368` passes: p95 **53.20ms**, p99 **157.84ms**, exactly
20 primary reads/s, zero unexpected API errors and no budget violations.
All 300 simulator/recorder fills, original fees and positions reconcile, along
with two identified funding bills. Peak application-container memory is 681.86 MiB;
four minute samples retain the same supervisor/engine/API PIDs. This short
measurement does not establish hour-scale retention or multi-day leak freedom.

Healthy-window supervisor logs contain one failed probe and one recovery, with
no replacement or signal dump. The transition timestamps span 1.052s. Its
one-second preceding context retains a 211.84ms loop-stall sample but no matching
slow completed ownership/fill/SQL span or GC record. That does not establish a
unique explanation of the timeout; missing samples are not proof of no delay.

Both roles report zero dropped file/queue records, zero suppressed incidents and
zero skipped metrics exports at the sampled end. Engine/API incident files use
about 1.48/0.94 MiB, within the original 32 MiB ceilings. Child-span omissions remain
explicit (13,337 engine/20 API); the rings also intentionally evict old context.
The probe did not saturate disk capacity; forced-budget tests verify reservation
and append behavior. A later hour may still exhaust bounded evidence capacity.

Final engine scalar distributions include startup and at most 12,000 retained
samples per family, so they are not the HTTP-window latency population:

| Phase | p99 | Maximum |
| --- | ---: | ---: |
| Ownership check | 7.66ms | 130.19ms |
| Fill recorder | 77.77ms | 464.33ms |
| Store new fill | 60.67ms | 153.30ms |
| Market-state checkpoint | 14.35ms | 52.29ms |

The outflow-advance counter covers 150,811 calls; its timing deque retains the
latest 12,000. Phase timings do not justify removing a fresh ownership check or
moving durable fill acknowledgment before commit. No runtime defect is closed
by these observations.

Measurement sources are frozen during the probe. The offline analyzer's
pre-timeout-context regression/refinement occurs afterward; its initial output,
refined output and respective code hashes remain distinct. The raw workload
result and incident streams are unchanged. Temporary containers/networks are
removed and the task's awake assertion is released. Demo container restoration
is not performed; its persistent volume is retained.

The earlier release and unexplained burst remain with their original image/run
scope. Neither temporal overlap nor a later passing probe establishes a unique
cause of those historical events. Slower daily reporting remains an accepted
scalability limit. Per-key reporting admission and long-history startup
aggregation remain candidates requiring their own measured bottleneck.

Raw evidence: `build/engine-probe-20261102/`; prior incident input:
`build/mm-load/20261101-215045-297059/`. The new analyzer adds an analysis file
without rewriting the original result, logs or incident streams.
