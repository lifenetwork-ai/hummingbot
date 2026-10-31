# Current-image release closeout

Status: **corrected R1 fails API/retention budgets; R2 is not started.** No release
closure is claimed. Application image `5973410d` / manifest `50a95f3a` is
unchanged; corrected diagnostic source is `2cb18eccc`.

2026-11-01 follow-up: bounded diagnostics identify additional on-loop metrics
export cost and verify a single-worker correction. A measured cache breakdown
defines new-run retention without reclassifying this failed result. Native114
and the matched300s recorder/shipping pair pass; full R1/R2 remain pending.
See [measurement follow-up](mm-measurement-remediation.md).

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
unchanged. The next causal checks are a bounded replay of the canceled aggregate
and a matched ordinary-entrypoint long diagnostic if needed; neither can be
labelled R2 or current release acceptance while R1 has not passed. A confirmed
runtime defect needs Red/Green remediation, not a speculative fix or blind hour
repeat.

Portable [evidence](mm-release-closeout-evidence.json) keeps the failed attempt,
controlled diagnostics and pending execution scopes separate.

Raw evidence: ignored `build/release-closeout-20261031/` and the run directories
above. R1/R2 remain open; the confirmed test-recorder GC defect is closed with
its own scoped verification.
