# Final-image probe timeouts and MM progress

Date: 2026-11-03 (Asia/Saigon).
[Evidence](mm-checkpoint-probe-correlation-evidence.json),
[checklist](../../docs/plans/MM_RUNTIME_LOGIC_AUDIT_TDD_PLAN.md#current-image-probemm-correlation--requested-2026-11-03).

The two engine probe episodes captured in a new 600-second diagnostic do **not**
show a matching complete MM pause: ticks, controllers and order submissions
continue. Small scheduling jitter remains. No additional runtime defect or
avoidable wait is demonstrated, so no speculative runtime patch, cache, pool
increase or probe-threshold change is introduced. This does not establish the
cause or trading impact of every historical timeout.

## Historical evidence and current measurement

All 21 retained episodes in the final R1 healthy envelope are reviewed: 20 engine
and one API, including one episode between measured windows. Three intersect
retained GC events and five intersect loop-stall samples; these sets can overlap.
Other contexts include observer, funding, recorder and PostgreSQL work. The
longest transition is approximately 2.76 seconds. R1 did not enable MM activity;
R2 retained ordinary supervisor logs without the detailed activity trace. Neither
can retrospectively prove tick, quote or order latency for those episodes.

The new run uses the same immutable final image `51d4d879d0f0`, source
`06ab51b17`, two application CPUs, 100,000 historical orders and fills, TTL15,
enhancements, volatility defense and operator reads. Existing opt-in activity,
incident, GC and market instruments are reused. There is no new instrumentation
or application source change. All 12 runner hashes remain unchanged; the 17
fixture hashes and image identity are retained in evidence.

The 600-second diagnostic passes unchanged load budgets: API p99 **71.41 ms**,
20 original-route reads/s, approximately 100 market updates/s, zero unexpected
API errors and **600 independently matched synthetic fills/fees/positions**.
These are simulator transactions, not OKX Demo fills. Nine process samples have
unchanged supervisor/engine/API PIDs; logs contain only the initial child starts,
with no child restart or unresponsive event. This short rich diagnostic does not
replace or rerun the [full R1/R2 release](../release/mm-checkpoint-final-release.md).

## Correlation at each timeout

The following maxima include intervals intersecting the estimated failed-request
start through recovery, with the next callback retained in the bounded context.
Request start is estimated from the supervisor marker minus measured probe time;
publication/rounding delay is unmeasured. These are interval correlations, not
precise request-to-quote causality. Start gaps include the configured loop cadence.

| Failure from window start | Failed probe | Failure-to-recovery marker interval | Tick start gap | Controller start gap | Successful submit returns / maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| 189.224 s | 301.67 ms | 1.042 s | 1.039 s | 1.022 s | 9 / 227.00 ms |
| 317.252 s | 301.50 ms | 1.030 s | 1.097 s | 1.049 s | 6 / 179.14 ms |

Both occur near scheduled executor refresh and order replacement. In the first
context, independent samples contain 3, 3, 11, 12 and 9 live orders. In the second,
one empty sample is 1.022 seconds before the failure marker; at 17.8 ms before
the marker and in subsequent samples there are 12 orders. Lifecycle records show
preceding refresh proposals, cancellation and terminal executor retirement.
There is no demonstrated probe-induced quote withdrawal. Samples do not prove
continuous or two-sided coverage between observations.

No retained GC or loop-stall sample intersects either new episode. The longest
retained observer spans are 174.72 and 144.44 ms; individual SQL/checkout spans in
those selected contexts are much shorter. Their overlap and the concurrent
replacement batches are useful leads, but neither observer span alone explains
the full probe timeout. A single exclusive cause is not established.

## Whole-window MM timing

| Measurement | p99 | Maximum |
| --- | ---: | ---: |
| Strategy tick start gap | 1,075.53 ms | 1,230.10 ms |
| Strategy tick execution | 40.44 ms | 81.32 ms |
| Controller start gap | 1,044.52 ms | 1,164.36 ms |
| Controller execution | 13.16 ms | 48.32 ms |
| Connector submit call-to-return | 184.25 ms | 227.00 ms |
| Connector cancel call-to-return | 231.28 ms | 513.98 ms |

All 600 completed ticks, 1,187 controller cycles, 138 submit returns and 138
cancel returns have successful recorded outcomes. Connector timings include
awaiting and scheduling; they are not decision-to-venue latency. Gaps between
order calls mostly reflect refresh policy and are not used as engine-pause
measurements. Percentiles use nearest rank on eligible in-window intervals.

The independent observer records 598 samples, 597 inside the exact window, with
no observer/clock/sink errors. Maximum sampling gap is approximately 1.05 s.
Twelve in-window samples have no aggregate live quotes; the longest consecutive
empty-sample first-to-last span is 1.004 s. This is not a continuous-outage or
two-sided coverage measurement. The engine's in-window export reports zero
activity sink/metadata errors, zero file drops and an empty writer queue. Recent
ring evictions are expected and explicit. The final engine activity-close summary
is absent, so completeness of the teardown flush is not certified.

## Probe policy and disposition

`ServiceHost.health()` reads local scalar state, without database/OKX work, but
executes on the engine's event loop. Work elsewhere on that loop can delay its
HTTP response. A 300 ms timeout is therefore an early responsiveness signal,
not an MM downtime measurement. `HealthWatchdog.observe()` records a warning
transition; it does not stop trading or revoke ownership on that first miss.
Sustained failure must reach the existing 15-second grace before supervision
terminates a child. Existing runtime/ownership/risk checks remain independent.

Keep both thresholds unchanged: these short recoveries do not justify masking
the signal or adding a separate health thread that could respond while the MM
loop is stuck. The measured subsecond jitter is retained explicitly. If future
incidents show materially enlarged tick/controller gaps, failed or slow order
operations, or unexplained quote absence, use these existing instruments to
identify the exact path and add Red/Green regressions before changing runtime.
No confirmed runtime fix was required in this investigation.

Raw evidence: `build/probe-mm-correlation-20261103/` and
`build/mm-load/20261103-185650-95efa2/`. The idle Demo service was stopped during
measurement and restored on the same image afterward: owner available, trading
stopped, no HALT/pending mutations, `/startup` and `/live` 200, `/ready` 503 for
intentional idle. Order/fill/position/executor hashes are unchanged. No account
repair, new connected trade, production trade or Cloud Run deployment occurred.
