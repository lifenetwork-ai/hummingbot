# Current-image release closeout

**Later logic audit:** [durable-HALT cleanup and fee-replay fixes](../investigations/mm-runtime-logic-audit.md)
are packaged in candidate `95cde31c`. Broad source and targeted image checks pass;
no new full release/load/soak is claimed. The historical full-gate images below
predate these fixes and should not be interpreted as current defect clearance.

Latest local-image follow-up: [`e2d393b1` status-publication fix and full R1/R2](../investigations/mm-engine-status-publication.md).
Residual probes remain; connected Demo is not rerun on this image.

Latest connected stopped-Demo follow-up: [`a190b024` full R1/R2 and stopped Demo upgrade](mm-submission-log-release.md).
The results below keep their original image scopes and are not relabelled.

Earlier follow-up (2026-11-01): **candidate `3f5cbd0f` first rerun fails burst
latency; the latest complete R1/R2 result is recorded below**.
See [the new run](#2026-11-01-latent-fix-candidate-rerun) and
[separate evidence](mm-latent-fixes-load-evidence.json). All earlier accepted/failed
results below remain tied to their original images and measurement sources.

Prior image `5973410d`: **R1 and R2 pass; scoped local verification is complete.**
Earlier failures and unresolved historical causes are preserved below. No
Cloud Run deployment or connected trading occurs in this closeout.
Application image `5973410d` / manifest `50a95f3a` is unchanged; measurement
source is frozen at `b7ba104e7`. The bounded follow-up has native114 and matched
300s recorder/shipping passes; see [measurement remediation](../investigations/mm-measurement-remediation.md).

Subsequent [historical-cause review](../investigations/mm-historical-cause-review.md) confirms and
fixes a test CPU/heartbeat timestamp defect (119 tests and a packaged old/new
comparison). H1/H2/H3 still lack a unique proven cause. The review does not
change the accepted image, frozen measurement source or R1/R2 results.

## Current full R1 — passed and independently verified

Run `20261101-011658-1465e8` completes the entire declared sequence and cleanup:

| Healthy window | Duration | p95 | p99 | Unexpected errors | Cumulative matched fills |
| --- | --- | --- | --- | --- | --- |
| Warmup | 300s | 121.97ms | 475.54ms | 0 | 300 |
| Mixed | 900s | 84.55ms | 436.39ms | 0 | 1,200 |
| Burst | 90s | 66.25ms | 264.65ms | 0 | 1,290 |
| Random soak | 3,600s | 147.21ms | 717.41ms | 0 | 4,884 |

All three Random profiles have actual running coverage. Venue quantities,
original fees and unique recorder identities reconcile; positions match.
Six injected cases pass: feed recovery, volatility defense, owner restart,
queued expiry, database delay and database failure. DB failure cases retain
HALT rather than resuming uncertain trading. Their planned interruptions remain
excluded from healthy latency. Independent verification checks the immutable
image, unchanged source hashes and complete scope; no passing windows are stitched.

Soak has58 continuous minute samples, maximum gap66.29s, same healthy supervisor/
engine/API identities and no unplanned child start/replacement or HALT.
Retention grows16.08MiB, raw cgroup current grows58.13MiB; **both** are below64MiB.
The declared new retention formula is `current - (file - shmem)`; this run would
also meet the conservative raw-growth threshold. Peak container memory is
760.76MiB, below1.5GiB. Engine/API median RSS grows10.00/5.90MiB; thread medians
stay fixed, FD medians grow2/0. No export is skipped in retained minute samples.

Stable identities do not mean zero stalls:98 healthy-sequence failed/recovered
probe episodes remain, longest4.591s. One bounded diagnostic signal captures the
engine at status `snapshot` assembly; a sampled frame alone does not prove the
whole episode's cause. The signal can interrupt a syscall and is an explicit
measurement intervention. This is a scoped passing load/recovery gate, not proof
of uninterrupted quoting or a closure of historical H1/H2/H3.

At healthy-sequence end, engine/API capture counts are125/87 out of128. The API
trace hits32MiB and drops2,684 sink records; engine sink drops zero. Bounded child
span truncation also remains explicit in portable evidence. GC pending/dropped
queues are zero. Traces are incomplete; do not infer absence of an event from a
missing record. Fault-time restarts have separate identities and summaries.

Temporary R1 containers are removed. The stopped Demo image/control/four ledger
fingerprints remain unchanged. R2 below uses this passing baseline with the ordinary shipping entrypoint and
matching fixtures/resources; short comparisons do not substitute for it.

## Matched shipping R2 — passed and independently verified

Run `production-control-20261101-024951-b873b2` uses the normal
`python -m hummingbot.cli.service` command, with no timing wrapper or diagnostic
signal recorder. The actual completed R1 baseline qualifies; image and required
YAML/client/exchange hashes match, as do2CPU/2GiB, PG caps, history100,000,
pool5 and TTL15. This is the planned shipping control, not a second full-hour gate.

| Window | Duration | p95 | p99 | Unexpected errors | Cumulative matched fills |
| --- | --- | --- | --- | --- | --- |
| Warmup | 300s | 52.24ms | 288.99ms | 0 | 300 |
| Mixed | 900s | 55.21ms | 396.39ms | 0 | 1,200 |

Independent verification confirms source hashes and identical supervisor/engine/
API identities before/after both windows and across their transition. No
unplanned child start/replacement occurs. Twelve probe episodes self-recover,
longest1.182s; no diagnostic stack signal is used. Peak container memory is
680.27MiB. This shorter shipping run does not establish a full-hour memory trend
or identify every earlier slowdown. It retains the same safe strategy and guards.

R1 and R2 checklist items are checked done. Temporary test containers are gone;
the existing stopped Demo image/control/four ledger fingerprints remain unchanged.
Only the task's own caffeinate process is stopped. Application runtime remains
unchanged fromfc0ee55b8; fixes here concern test instrumentation, accounting and
acceptance scope. The local artifact qualifies under the declared protocol.
This is not proof of zero transient stalls, universal absence of memory leaks,
or attribution of historical H1/H2/H3 to test infrastructure. No speculative
runtime/cache/pool change is introduced to explain them.

## Preparation and preserved failures

The operator requested R1/R2 closeout on 2026-10-31. The machine is on AC power
with the lid open. Existing Demo remains stopped on its original image/control
and four ledger fingerprints. Tests use isolated fake-exchange/PostgreSQL
networks and dummy credentials. No deploy or connected trading is performed.

The initial attempt `20261031-221920-6c4d3f` is deliberately interrupted before
any complete window to configure the long-run recorder. Test-only capture count
is explicitly128, with unchanged 512KiB ring / 32MiB stream caps per role.
Default short-run capacity remains32. Fifteen new configuration/boundary tests
bring the related suite to76. Commit `d61405c62`; its preparation interruption
is retained and is not a runtime failure or an accepted shortened run.

The subsequent R1 attempt `20261031-222312-6f060d` passes warm300/mixed900/burst90:
p99 226.20/245.14/262.03ms, zero unexpected errors and 1,290 matching fills at
the end of burst. It then fails availability during healthy soak:

| UTC | Observation |
| --- | --- |
| 15:55:37.486 | Last retained old-engine fill log |
| 15:55:38.616 | First engine probe timeout |
| 15:55:54.780 | Supervisor declares unresponsive after13 failures over16.165s |
| 15:55:57.917 | Old engine exits with SIGKILL after the existing shutdown grace |
| 15:55:58.179 | Replacement engine starts; PID31 becomes1974, API PID32 remains |
| 15:56:05.790 | Restored exposure fails position verification; recovery retains HALT |

The soak minute samples independently show the changed engine PID and HALT.
The agent interrupts this irrecoverably failed healthy gate after observing
them; no full hour/fault acceptance is claimed. R2 is not run from this failure.
Uncertain position recovery is not bypassed or cleared to force a pass.

The old engine's Python recorder produces no retained sample after15:55:29.649.
API samples continue, with most later shared CPU growth accounted for by the
API itself. This does not establish a CPU-intensive engine loop, host sleep or
an OKX outage. It exposes a diagnostic blind spot: a Python sampling thread
cannot reliably flush its own history if its process is blocked on recorder
locks. The specific failed run has no final old-engine stack to prove its exact
blocked call. The confirmed defect and controlled reproduction below are
reported separately rather than retroactively declaring every old cause solved.

## Confirmed test-recorder GC deadlock — closed, verified

The old GC callback synchronously calls `emit_trace`, which enters the incident
buffer. A collection can start during JSON/container allocation while that same
buffer's nonreentrant lock is already held. The callback then waits on the lock
owned by its own interrupted call. The sampling thread deadlocks; the engine
loop subsequently blocks when it exports recorder state or emits a slow trace.

An isolated native reproduction extracts the actual old callback functions from
source and forces a real collection inside capture decoding. It deadlocks and
is killed after3s. New regressions initially fail Red. The corrected callback
only appends scalar events to a bounded deque; the watchdog drains them outside
GC and recorder critical sections. No GC policy or production trading behavior
changes. The expanded related suite passes **81 cases**.

A bounded signal fallback is also added to the test supervisor. After a child
has been healthy and then fails three consecutive probes, its existing Popen
handle requests a SIGUSR2/faulthandler stack dump to stderr. Maximum16 dumps per
role; replacement children must become healthy before being eligible. This
does not alter the probe result, 300ms timeout, failure grace, ownership or HALT.
The signal is a diagnostic intervention and can interrupt a syscall; its use
must remain visible when interpreting timing. It is absent from shipping code.

| Packaged diagnostic | Scope and outcome |
| --- | --- |
| `20261031-231135-c87850` | Counterfactual inline GC emitter, forced collection inside the buffer lock: engine becomes unresponsive after13 failed probes over15.934s. Signal fallback captures the lock/GC reentry chain; intentionally fails, never release acceptance. |
| `20261031-231434-d15615` | Deferred GC emitter, forced collection in both engine/API: returns normally, 90 matching fills, zero unexpected errors, p99 72.82ms, unchanged process identities; diagnostic pass. |

Both use the same application image and an explicitly modified test fixture.
The counterfactual stack shows `trigger -> forced GC -> inline callback ->
emit_trace -> IncidentBuffer.emit` waiting on the recorder lock. This proves
the diagnostic deadlock mechanism and its correction. It is not a literal replay
of the original failed soak or a closure of historical H1/H2/H3.

Fix commit: `2cb18eccc`. The production application, connector, controller,
migration and build sources remain unchanged from the frozen candidate source.

## Corrected R1 and R2

R1 is restarted with the canonical corrected fixture, **without** forced-GC
hooks or the counterfactual class. Durations, rates, history, zero-error and
latency/retention thresholds remain unchanged; the immutable image is retained.
Only a complete green R1 permits the matching ordinary-entrypoint R2. Separate
passing windows will not be combined into acceptance. Historical failed runs
and unresolved causes remain explicit.

### Completed corrected run — failed, preserved

Run `20261031-231819-7fe2f2` completes all four measured windows on the canonical
fixture. The source hashes remain unchanged. Warm300/mixed900/burst90 pass:
p99 84.30/163.34/244.23ms, zero unexpected errors and 1,290 reconciled fills.
The full3600s Random soak then fails the declared budgets:

| Measure | Observed result |
| --- | --- |
| Primary reads | 72,000 HTTP200; p95 510.30ms, p99 1,471.80ms |
| Auxiliary reads | 18,000 HTTP200; p95 508.38ms, p99 1,465.73ms |
| Unexpected application results | 13 explicitly degraded daily-statistics responses over4.51s; no HTTP error responses |
| Ledger | 4,885 cumulative unique exchange/recorder fills; positions match |
| Runtime | Same supervisor1/engine32/API33; no unplanned replacement or HALT |
| Minute continuity | 57 samples, maximum gap66.78s |
| Container retention | Last-five minus first-five median70.68MiB; fails unchanged64MiB budget |
| Per-process retention | Engine+10.75MiB, API+4.04MiB, supervisor+0.125MiB; no median thread/FD growth |

All three profiles have actual running coverage. A stable PID does **not** mean
there were no stalls: the full service log has195 failed/recovered probe episodes,
the longest recovered engine episode6.476s. The bounded signal fallback fires15
times; its diagnostic intervention stays explicit. No `child_unresponsive`
replacement occurs. This is useful liveness/ledger evidence, not a release pass
or proof of uninterrupted quote execution.

The retained API trace catches `QueryCanceled` on the daily fill aggregation:
SQL wall time2,196.22ms, worker thread CPU14.58ms. The API then serves its existing
explicit last-good fallback. This establishes the cancellation/fallback path for
the degraded cluster; it does not identify why the database query became slow,
nor explain every route's latency or historical H1/H2/H3. It is separate from
the previously reproduced recorder GC deadlock. Do not change query deadlines,
probe grace or zero-error acceptance just to convert this result to green.

Late passive cgroup observations show anonymous memory715MB approximately stable
while file cache grows from61.3MB to67.0MB. Both trace files eventually reach the
original32MiB cap. There is no first-five-sample cgroup breakdown or controlled
cache experiment, so attributing the entire70.68MiB increase to trace is not
proven. The modest process RSS changes and bounded threads/FDs also do not prove
a production memory leak. Report the failed container-retention check separately.

The recorder is deliberately bounded: final engine/API sink drop counters are
retained in portable evidence. Later records are missing after the32MiB caps;
GC pending/dropped-event counters remain zero. Do not describe these traces as
complete or equate every dropped trace record with an application error.

The runner exits on these failed budgets before six fault cases. R2 is not run
from this failed baseline. Temporary test containers are removed; read-only
fingerprints confirm the existing stopped Demo image/control/four ledgers are
unchanged. At that checkpoint the proposed causal checks were a bounded aggregate replay
and a matched ordinary-entrypoint diagnostic if needed; neither can be
labelled R2 or current release acceptance while R1 has not passed. A confirmed
runtime defect needs Red/Green remediation, not a speculative fix or blind hour
repeat.

Portable [evidence](mm-release-closeout-evidence.json) keeps the failed attempt,
controlled diagnostics and the subsequent completed R1/R2 scopes separate.

Raw evidence: ignored `build/release-closeout-20261031/` and the run directories
above. Historical failed results remain failed; the current R1 result supersedes
execution status only. Current R1/R2 verification is complete; historical causes stay explicit.


## 2026-11-01 latent-fix candidate rerun

- [x] Remove retired bot images `4971ea59` and `5973410d` and redundant Demo
  aliases. Preserve every running/stopped container's image, PostgreSQL/registry
  test dependencies and both persistent Demo containers/volumes. Unique image
  count falls7→5; layers shared by the retained candidate remain, so this does
  not reclaim the sum of their displayed image sizes.
- [x] Pin candidate `3f5cbd0f` / manifest `6e54a348`, freeze 1,221 tracked source
  inputs and run the same all-feature workload with 2 CPU/2 GiB, 100,000 history
  fills, TTL15 and 128 bounded incident captures. Source changes are the four
  [verified latent-bug fixes](../implementation/life-mm-logic-review.md#2026-11-01-latent-bug-audit-and-fixes),
  on top of the earlier three flow optimizations. No production YAML is changed.
- [x] Complete unchanged warm300/mixed900/burst90/Random-soak3600 and six fault
  cases; independently verify zero errors, latency, memory/continuity, ledger,
  process identities and frozen-source scope. Passing run `20261101-063816-31af48`;
  the first failed run is retained below.
- [x] After green full R1, run the ordinary shipping entrypoint with matching
  warm300/mixed900 resources/fixtures; independently verify the R2 scope.
- [x] Verify cleanup and Demo preservation; record final evidence and outcome.

The foreground runner holds its own sleep assertion while the gate runs. Host
sleep or a failed budget invalidates the result; durations, fill rates and
thresholds are unchanged. Existing source-level tests and three targeted E2E
passes on this image retain their separate scope. The completed new-image
results below were measured independently; prior release results remain
tied to their original image.


### Preserved first rerun — burst latency failure

Run `20261101-061242-5839e1` passes warm300 and mixed900 with primary p99
156.44/265.96ms and zero unexpected errors. Burst90 fails primary p95/p99
1,116.52/1,875.36ms and auxiliary1,199.64/1,985.71ms against unchanged 500/1,000 ms
budgets. Burst delivers 90 fills, cumulative 1,290 unique fills/fees and positions
match, drain is 3.45 s and peak memory 719.44 MiB. Engine/API keep their PIDs and no
HALT/replacement is observed, but engine probe timeouts self-recover; this does
not prove uninterrupted quoting. Soak/faults are not executed and R2 is skipped.

A retained daily-statistics request takes 1,967.9 ms, including sequential 715.1 ms
and 790.7 ms SQL spans; latency is also high across other routes and client
scheduling. This is evidence of a slow request, not proof that the same SQL
caused every slow request or engine stall. No pool/cache/runtime change is made
from this observation. The host remained on AC. Host/video/GC causation is not proven; the
external OKX service is outside this isolated simulator test path. All 1,221 frozen source hashes still match.

A fresh full rerun keeps the same image/manifest, flags, rates, durations and
budgets. This failure remains in the separate evidence even if the next run
passes; no source bug or historical cause is declared fixed by a later pass.


### Latest completed rerun — scoped result

Latest full R1 and matched shipping R2 pass; the first burst failure remains unexplained.

Application image `3f5cbd0f` / manifest `6e54a348` is unchanged. Latest full
R1 is `build/mm-load/20261101-063816-31af48`. All 1,221 tracked source inputs retain their frozen hashes;
no runtime, pool/cache policy, rate, duration or budget is changed during reruns.

| Healthy window | Duration | p95 | p99 | Unexpected errors | Cumulative matched fills |
| --- | --- | --- | --- | --- | --- |
| warmup | 300s | 64.97ms | 338.91ms | 0 | 300 |
| mixed | 900s | 144.22ms | 718.24ms | 0 | 1200 |
| bursts | 90s | 77.81ms | 285.11ms | 0 | 1290 |
| soak | 3600s | 70.67ms | 337.97ms | 0 | 4884 |

Soak has 58 minute samples, maximum gap 65.99 s.
Retention grows 13.53 MiB, raw cgroup grows 35.64 MiB;
peak is 752.14 MiB. The unchanged
64MiB retention / 1.5 GiB peak and continuity gates pass. Healthy-sequence
process identities remain stable; per-role RSS/thread/FD growth is recorded
in the portable evidence. This is a one-hour observation, not proof of universal
absence of memory leaks.

Healthy-sequence logs retain 57 recovered probe episodes, maximum 2.680 s.
No diagnostic stack signal was requested. Stable PIDs and matching fills do not
imply uninterrupted quoting. At healthy end, both trace sinks have zero dropped
records, but engine/API child-span truncations are 317,725/14,864; normal bounded
prehistory evictions are 19,360/22,137. No metric export is skipped, and pending/
dropped GC queues are zero. These captures are incomplete; all loss counters
remain in the evidence. Missing trace records do not establish absence of
a slow operation or identify a unique historical cause.

Random actually draws `follow_market`, `follow_token`.
`sideways` is not drawn in this hour: no long-soak coverage of that profile is
claimed. Uniform Random may repeat profiles; no seed/pool/duration is changed
to force coverage. The earlier same-image nine-case E2E matrix retains its
separate all-three-profile verification.

All six injected faults pass: feed, volatility defense, owner restart, queued
expiry, database delay and database failure. Planned fault interruptions are
excluded from healthy latency; uncertain database/ownership effects retain HALT.

Matched shipping R2: `build/service-supervision/production-control-20261101-080809-a584fd`. It uses the normal
`python -m hummingbot.cli.service` entrypoint with matching image, catalogue,
client/exchange fixtures and resources, without the timing/incident wrapper.

- Warmup (300 s): p95 54.03 ms, p99 307.85 ms; 0 unexpected errors; 300 matched cumulative fills.
- Mixed (900 s): p95 55.36 ms, p99 253.37 ms; 0 unexpected errors; 1,200 matched cumulative fills.

R2 is a matched warm300/mixed900 control, **not a second full-hour soak**. Its
raw diagnostic/full-scope flags remain unchanged; independent verification
checks baseline eligibility, budgets, fixture/resource matches and identities
before/after/between windows. Internal SQL/worker/GC counters are not measured
in this shipping control. Peak container memory is 682.23 MiB. Its healthy logs
retain 6 recovered probe episodes, longest 1.215 s, with no unplanned child
replacement/start or HALT observed.

The first attempt's burst p99 remains 1,875.36 ms and its cause remains open.
A later pass does not fix/explain it, attribute it to host/video/GC/OKX, or erase
it. These are isolated internal-simulator tests, with no public OKX calls or
connected Demo trading. Historical H1/H2/H3 remain unproven as before.

The runner's own test containers/networks and sleep assertion are cleaned.
Demo ledger/control/image preservation is checked read-only; its existing
`e526118b` image and all persistent volumes are retained. No Cloud Run deployment
or new full E2E/upgrade matrix is claimed. [Portable evidence](mm-latent-fixes-load-evidence.json)
contains both attempts, independent checks, matching ledgers, supervision,
resource/coverage limits and raw-result hashes.
