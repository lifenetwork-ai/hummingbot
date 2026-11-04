# Current-image probe latency and MM progress

2026-11-04 (Asia/Saigon). Shipping image `086454de`, source `0f4095671`;
host harness `f40cc9a46`. **The captured measured probe does not show a matching
MM stop. No confirmed avoidable runtime defect is patched.** Short MM jitter,
refresh/protective quote gaps and recurring spot SELL invalidations remain visible.

[Evidence](mm-current-probe-correlation-evidence.json),
[checklist](../../docs/plans/MM_OWNER_PUBLICATION_TIMEOUT_INVESTIGATION.md#current-image-mmprobe-correlation--requested-2026-11-04).

## Scope and verification

Reuse the existing bounded MM activity, incident/GC capture and independent
simulator quote sampler. Fixed `follow_market`, spot/perpetual both, 600 seconds,
100,000 historical orders and fills, two application CPUs, enhancements,
volatility defense and TTL15. Original runtime deadlines, guards, workload rates
and acceptance thresholds remain intact. No full tracing, operator-read addition,
artificial stall or accelerated refresh is introduced. The corrected full-service
offline history preparation is used, with ownership generation 1→2.

The diagnostic passes: **13,200 HTTP 200 responses**, no unexpected API errors or
stale responses, primary/auxiliary p99 **347.99/118.91ms** and **600 exact
synthetic fills/original fees/positions**. These are simulator fills, not actual
OKX Demo fills. Nine minute samples preserve the engine PID; retained logs contain
no unexpected service failure or child restart. This opt-in fixed-profile run
does not replace the completed uninstrumented Random hour or certify its old
per-tick timings. Its different duration/instrumentation does not establish an
API-performance regression or improvement against that hour.

## Probe correlation

There are two recovered engine probes in the complete container log: one before
the measured window, and **one inside it**. Do not count the preparation episode
as a second measured case. The in-window event begins about 49.077 seconds after
window start: failed probe **318.874ms**, failure-marker to recovery-marker
**1.024s**. No failure-triggered restart or quote withdrawal is demonstrated.

In the estimated failed-request-through-recovery context, with the next callback
retained, strategy tick start gap is at most **1,050.67ms**, tick execution at most
**24.05ms**; controller start gap is at most **1,233.10ms**, execution at most
**150.62ms**. Ticks and controller calls return successfully. In the narrower
failure-marker-through-recovery context, controller gap is 1,152.65ms. The
measurement boundaries are different and must not be silently combined.

Five independently clock-aligned simulator samples around the incident contain
12 live orders, with BUY and SELL on both markets. No submit/cancel return falls
inside that selected incident context; orders are resting, so a new send every
second is not expected. Whole-window order timing below cannot be substituted
for missing per-incident sends. Samples do not certify continuous presence or
fresh price between observations.

A 132.42ms candle-sample/NATR operation precedes the failure marker; a 202.74ms
observer operation overlaps the transition, with smaller SQL spans. No retained
GC or loop-stall sample intersects this case. These are overlapping leads, not
exclusive attribution of the full timeout. The request start is estimated from
marker time minus probe elapsed; publication delay remains unmeasured.

## Whole-window MM timing

Percentiles use nearest rank. Start-gap calculations retain the previous start
inside the same window; execution durations require an in-window start.

| Measurement | p99 | Maximum |
| --- | ---: | ---: |
| Strategy tick start gap | 1,106.51ms | 1,237.52ms |
| Strategy tick execution | 60.70ms | 89.47ms |
| Controller start gap | 1,104.46ms | 1,570.78ms |
| Controller execution | 27.15ms | 568.53ms |
| Successful connector submit call-to-return | 285.12ms | 443.20ms |
| Successful connector cancel call-to-return | 116.08ms | 136.22ms |

All 600 completed ticks and 1,184 controller cycles return successfully.
Connector submissions have 219 successful returns and one `MarketReadinessBlocked`
exception; its order ID is absent from the simulator accepted ledger. All 219
cancel calls return successfully. These are connector-call durations, not full
decision-to-terminal-venue RTT or actual OKX latency. Long gaps between sends
mostly reflect resting-order/refresh policy and are not engine-stall durations.

The longest controller cycle ends at +55.742s. It overlaps a 992.49ms
`service.observe_once` span, a publication SELECT of 87.30ms and UPDATE of 21.34ms.
The observer's inclusive event-loop thread CPU is 482.66ms; it includes work by
other coroutines across awaits and cannot be assigned exclusively to the observer.
No retained inner span/stack establishes the unique controller bottleneck. This
is measured jitter to retain, not proof that a cache, pool increase or a specific
SQL rewrite would fix it. No speculative runtime patch or Red/Green fix is claimed. The existing activity,
analysis, simulator and wiring regression modules pass **47 tests** after the
diagnostic; this is validation of the reused measuring tools, not a runtime fix.

## Independent quote observations

The observer retains 598 samples total, **597 within the exact window**, no read,
clock, sink or file-drop errors. In-window maximum sample spacing is 1,094.27ms;
whole-observer maximum is 1,114.44ms. Live quote identity tracking is complete.

| Point-sample condition | Samples / 597 |
| --- | ---: |
| Spot has BUY and SELL | 494 |
| Perpetual has BUY and SELL | 574 |
| Both markets have BUY and SELL | 490 |
| No aggregate live order | 13 |

These are sample fractions, not elapsed-time uptime. The thirteen empty samples
form seven groups. Five have preceding executor-refresh proposals; two have
readiness/admission withdrawal, including one mixed refresh/spread-invalid group.
The longest consecutive empty-point first-to-last span is **2.004s**, not a
measured continuous-outage bound. The longest sampled spot-SELL-absence group
spans 4.013s and mixes refresh, spread-invalid and readiness changes.

Many other short spot SELL absences recur near `spread_quote_invalid` proposals,
while controllers report ready/admitted and submissions continue. There are
46 sampled spot-SELL-absence groups in total, including normal refresh and guard
groups; this is not 46 unexplained engine failures. The validator combines
level spread, current composed size and fee floor, and does not record a
component-level rejection reason here. **Recurring SELL churn is an optimization
lead requiring a deterministic component-level replay before a policy/runtime
change.** This run does not prove a price-rounding or inventory-skew defect.
Do not weaken fee/size/admission safeguards to increase the coverage count.
A production two-sided-coverage SLO must explicitly separate replacement and
protective pauses; this diagnostic does not declare that SLO satisfied.

## Evidence loss, disposition and restoration

The whole-window activity archive has zero writer drops, zero sink/metadata
errors and an empty queue at the final window export. The selected episode has
no evidence overflow or observer clock/tracking errors. Incident context and
child spans are intentionally bounded: engine/API omitted-span counters are
20,859/39, and rolling incident/ring evictions are retained. Therefore the full
internal call tree is not available even though activity records are retained.

`mm-activity-summary.json` predates the measured window and belongs to the clean
preparation shutdown. It is not used as proof of the final measured-engine flush.
The final measured export supplies writer counters; teardown flush completeness
is not certified. Raw close-summary age and all file hashes are retained.
The first one-off evidence-export attempt rejected a nullable previous-start
value; correcting that postprocessor did not change the workload/runtime or
rerun acceptance. Its original script is retained in the raw directory.

Keep the 300ms health signal, supervision grace, ownership fences, durability
and all risk guards unchanged. This measured probe shows MM progress with normal
cadence plus jitter; it does not demonstrate an avoidable engine-stop defect.
The 568ms controller outlier and repeated spot SELL invalidation remain separate
leads, not proven causes of the eight previous Random-hour events or its 3.798s
observation interval. Stronger claims need the corresponding instrumented event.

Actual Demo is restored stopped on the same image, owner available, no HALT,
pending mutation or unresolved order. All four counts/hashes are unchanged:
3,513 orders, **23 fills**, three positions and 2,848 executors. A private backup
was taken before isolating resources. No credentials/private config change,
new actual Demo trade, image rebuild, full release rerun or deployment occurs.

Raw run: `build/mm-load/20261104-190227-81e4bb/`;
analysis/restoration: `build/mm-probe-current-correlation-20261104T120222Z/`.
